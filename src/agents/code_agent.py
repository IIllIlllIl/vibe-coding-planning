"""Code generation agent.

The Agent edits files and emits the shared completion marker followed by its
configured payload: a staged diff, or JSON containing separate code/test diffs.
The Agent owns the semantic staging choice. DefaultAgent strips the marker,
preserving the payload; the Host validates but never filters or repairs it.
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
from src.exceptions import AgentTaskError, CommandTimeoutError
from src.agents.plan_submission import (empty_patch_error, submission_feedback,
    SPLIT_CODE_PROTOCOL, SPLIT_CODE_CONTRACT, validate_split_code_submission, split_patch_digests)

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


def _extract_result(
    exception_name: str,
    exception_msg: str,
    trajectory: list[dict[str, Any]],
) -> str:
    """Extract the agent's final output based on how it terminated.

    Only ``Submitted`` is treated as success — its message is the
    declared payload (already with the marker line stripped
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
        trajectory=trajectory,
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
    allow_empty_submission: bool = False,
    validate_submission_in_loop: bool = False,
    submission_protocol: str = "staged_diff_v1",
    code_retry_context: str = "",
) -> tuple[str | dict[str, str], list[dict[str, Any]]]:
    """Run the code generation agent.

    Args:
        config: Full configuration object.
        plan: The plan text produced by the plan agent.
        issue_description: The original SWE-bench issue description.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(submission, trajectory_messages)``: the exact staged diff
        or a dict of exact code_patch/test_patch strings for split_code_test_v1.
        No Host path policy, semantic filtering, or patch repair is applied.
        Callers may explicitly allow an empty formal submission when
        the evaluator owns that outcome classification.

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
    split = submission_protocol == SPLIT_CODE_PROTOCOL
    if split:
        instance_template = (instance_template or "{{task}}") + "\n" + SPLIT_CODE_CONTRACT
    if code_retry_context:
        instance_template = (instance_template or "{{task}}") + "\n<previous_submission>\n{{code_retry_context}}\n</previous_submission>"

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.api_key,
        api_base=config.system.api_base,
        temperature=config.agent.temperature,
        thinking=config.agent.thinking,
    )
    if model_wrapper is not None:
        model = model_wrapper(model)

    def validate_patch(payload):
        if split:
            return validate_split_code_submission(payload, allow_empty=allow_empty_submission,
                                                  digests=split_patch_digests(env))
        error = empty_patch_error(payload, allow_empty=allow_empty_submission)
        if error:
            raise ValueError(error)

    agent = build_default_agent(
        DefaultAgent,
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
        instance_template=instance_template,
        **({"submission_validator": validate_patch,
            "submission_error_feedback": lambda error: submission_feedback(error, phase="code")}
           if validate_submission_in_loop or split else {}),
    )

    logger.info(
        "Starting code agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    try:
        with _phase_timer(phase_timeout_seconds):
            exception_name, exception_msg = agent.run(
                task=issue_description, plan=plan,
                **({"code_retry_context": code_retry_context} if code_retry_context else {})
            )
            raise_for_permanent_provider_error(exception_name, exception_msg)
    except CommandTimeoutError as exc:
        error = AgentTaskError(
            str(exc),
            phase="code",
            reason="code_command_timeout",
            trajectory=agent.messages,
        )
        _write_failure_trajectory(
            failure_trajectory_path,
            agent.messages,
            "CommandTimeout",
            str(exc),
        )
        raise error from exc
    except AgentTaskError as exc:
        exc.trajectory = list(agent.messages)
        _write_failure_trajectory(
            failure_trajectory_path,
            agent.messages,
            "PhaseDeadlineExceeded",
            str(exc),
        )
        raise
    except Exception as exc:
        # Operational verification failures retain the completed investigation
        # without being reclassified as Agent output errors.
        exc.trajectory = list(agent.messages)
        raise
    if exception_name != "Submitted":
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
    patch_text = _extract_result(exception_name, exception_msg, agent.messages)
    if split:
        try:
            return validate_patch(patch_text), agent.messages
        except ValueError as exc:
            raise AgentTaskError(str(exc), phase="code", reason="code_invalid_split_submission",
                                 trajectory=agent.messages) from exc
        except (OSError, RuntimeError) as exc:
            exc.trajectory = list(agent.messages)
            raise

    if (not patch_text or not patch_text.strip()) and not allow_empty_submission:
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
        raise AgentTaskError(
            "Code agent produced empty output.",
            phase="code",
            reason="code_empty_patch",
            trajectory=agent.messages,
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
