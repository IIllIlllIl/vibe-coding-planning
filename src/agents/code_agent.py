"""Code generation agent.

Aligns with the mini-swe-agent submission protocol: the agent edits files
in the container with shell commands and finally emits
``echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`` followed by the configured
``git diff --cached`` command. ``DefaultAgent.has_finished`` strips the
marker line and ``Submitted.exception_msg`` carries the canonical diff
output verbatim — no fence-stripping, validation, or repair is needed.
"""

from __future__ import annotations

import logging
import json
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
import signal
import threading
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.config import Config
from src.exceptions import AgentTaskError

logger = logging.getLogger(__name__)


@contextmanager
def _phase_timer(timeout_seconds: float | None):
    """Interrupt an agent phase before the enclosing Slurm allocation expires."""
    if timeout_seconds is None or timeout_seconds <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Code phase timer requires the worker main thread")
    if not hasattr(signal, "setitimer"):
        raise RuntimeError("Code phase timer requires POSIX setitimer support")

    def deadline_reached(signum: int, frame: Any) -> None:
        del signum, frame
        raise AgentTaskError(
            f"Code agent exceeded its {timeout_seconds:g}s phase budget.",
            phase="code",
            reason="code_phase_deadline_exceeded",
        )

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, deadline_reached)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _extract_result(exception_name: str, exception_msg: str) -> str:
    """Extract the agent's final output based on how it terminated.

    Only ``Submitted`` is treated as success — its message is the
    ``git diff --cached`` output (already with the marker line stripped
    by ``DefaultAgent.has_finished``). Any other exit status (e.g.
    ``LimitsExceeded``) means the agent did not produce a real
    submission; raising :class:`TaskError` lets the pipeline record an
    explicit failure rather than fabricate a patch from the unfinished
    conversation history.
    """
    if exception_name == "Submitted":
        return exception_msg
    reason = (
        "code_step_or_cost_limit"
        if exception_name == "LimitsExceeded"
        else "code_not_submitted"
    )
    raise AgentTaskError(
        f"Code agent terminated without a submission "
        f"(exit_status={exception_name}): {exception_msg[:200]}",
        phase="code",
        reason=reason,
    )


def run(
    config: Config,
    plan: str,
    issue_description: str,
    env: Any,
    *,
    model_wrapper: Callable[[Any], Any] | None = None,
    failure_trajectory_path: Path | None = None,
    phase_timeout_seconds: float | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the code generation agent.

    Args:
        config: Full configuration object.
        plan: The plan text produced by the plan agent.
        issue_description: The original SWE-bench issue description.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(patch_text, trajectory_messages)``. ``patch_text``
        is the raw ``git diff --cached`` output captured by the
        official submission command; no post-processing is applied.

    Raises:
        TaskError: If the agent terminates without submitting (e.g.
            step/cost limit) or produces empty output.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    # Pass the raw system_template verbatim. The {{plan}} Jinja placeholder
    # is rendered at agent.run() time via the extra_template_vars kwarg
    # below — never inlined into the template source on the host side (that
    # would cause mini-swe-agent's second-pass Jinja render to crash on any
    # plan content with {{...}} or {%...%} fragments, which Django/Sphinx/
    # Sympy bug plans regularly contain).
    system_template = config.prompts.code_generation_prompt
    instance_template = config.prompts.code_instance_template or None

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.api_key,
        api_base=config.system.api_base,
        temperature=config.agent.temperature,
    )
    if model_wrapper is not None:
        model = model_wrapper(model)

    agent = build_default_agent(
        DefaultAgent,
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
        instance_template=instance_template,
    )

    logger.info(
        "Starting code agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    try:
        with _phase_timer(phase_timeout_seconds):
            exception_name, exception_msg = agent.run(
                task=issue_description, plan=plan
            )
            raise_for_permanent_provider_error(exception_name, exception_msg)
    except AgentTaskError as exc:
        _write_failure_trajectory(
            failure_trajectory_path,
            agent.messages,
            "PhaseDeadlineExceeded",
            str(exc),
        )
        raise
    if exception_name != "Submitted":
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
    patch_text = _extract_result(exception_name, exception_msg)

    if not patch_text or not patch_text.strip():
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
        raise AgentTaskError(
            "Code agent produced empty output.",
            phase="code",
            reason="code_empty_patch",
        )

    return patch_text, agent.messages


def _write_failure_trajectory(
    path: Path | None,
    messages: list[dict[str, Any]],
    exit_status: str,
    exit_message: str,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "exit_status": exit_status,
                "exit_message": exit_message,
                "messages": messages,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
