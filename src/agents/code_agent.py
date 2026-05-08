"""Code generation agent.

Aligns with the official mini-swe-agent SWE-bench submission protocol:
the agent edits files in the container with shell commands and finally
emits ``echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git
diff --cached``. ``DefaultAgent.has_finished`` strips the marker line and
``Submitted.exception_msg`` carries the canonical ``git diff --cached``
output verbatim — no fence-stripping, validation, or repair is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
)
from src.config import Config
from src.exceptions import TaskError
from src.prompts.templates import render_code_prompt

logger = logging.getLogger(__name__)


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
    raise TaskError(
        f"Code agent terminated without a submission "
        f"(exit_status={exception_name}): {exception_msg[:200]}"
    )


def run(
    config: Config,
    plan: str,
    issue_description: str,
    env: Any,
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

    system_template = render_code_prompt(
        config.prompts.code_generation_prompt, plan
    )
    instance_template = config.prompts.code_instance_template or None

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.deepseek_api_key,
        api_base=config.system.api_base,
    )

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

    exception_name, exception_msg = agent.run(task=issue_description)
    patch_text = _extract_result(exception_name, exception_msg)

    if not patch_text or not patch_text.strip():
        raise TaskError("Code agent produced empty output.")

    return patch_text, agent.messages
