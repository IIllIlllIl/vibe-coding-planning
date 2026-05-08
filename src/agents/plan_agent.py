"""Plan generation agent.

Uses DefaultAgent's interactive step loop so the agent can explore the
codebase (via cat, grep, ls, etc.) before producing the structured Plan
text. The agent writes the plan to ``/tmp/plan.md`` and submits with
``echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat /tmp/plan.md``;
the host then reads the file directly so empty/whitespace plans surface
as TaskError rather than silently passing downstream.
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
from src.prompts.templates import render_plan_prompt

logger = logging.getLogger(__name__)


def _extract_result(agent: Any, exception_name: str, exception_msg: str) -> str | None:
    """Extract the agent's final output based on how it terminated.

    On a clean ``Submitted`` exit, ``exception_msg`` is already the
    submitted text with the ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``
    marker line stripped by ``DefaultAgent.has_finished`` — no extra
    sanitisation is needed. Any other exit (e.g. ``LimitsExceeded``)
    returns None so the pipeline can raise a clear error instead of
    passing an unfinished / invalid plan downstream.
    """
    if exception_name == "Submitted":
        return exception_msg.strip()
    return None


def _read_plan_from_file(env: Any) -> str | None:
    """Try to read /tmp/plan.md from the Docker container.

    Returns:
        The file content if successfully read and non-empty, otherwise None.
    """
    try:
        result = env.execute("cat /tmp/plan.md")
        if result.get("returncode") == 0:
            content = result.get("output", "").strip()
            if content:
                return content
    except Exception:
        pass
    return None


def run(
    config: Config,
    issue_description: str,
    env: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the plan generation agent.

    Args:
        config: Full configuration object.
        issue_description: The SWE-bench issue description. Forwarded to
            DefaultAgent as ``task``; the configured
            ``plan_instance_template`` Jinja-renders ``{{task}}`` into the
            first user message.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces an empty or invalid plan.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    system_template = render_plan_prompt(
        config.prompts.plan_generation_prompt,
        config.prompts.nrpv_block,
    )
    instance_template = config.prompts.plan_instance_template or None

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
        "Starting plan agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    exception_name, exception_msg = agent.run(task=issue_description)

    # Try to read plan from the file the agent wrote in the container
    plan_text = _read_plan_from_file(env)
    if plan_text is None:
        plan_text = _extract_result(agent, exception_name, exception_msg)

    if not plan_text or not plan_text.strip():
        if exception_name == "Submitted":
            raise TaskError(
                "Plan agent submitted but /tmp/plan.md was empty and no plan text was returned."
            )
        raise TaskError(
            f"Plan agent terminated without a submission (exit_status={exception_name}). "
            f"Expected the agent to write a plan to /tmp/plan.md and finish with: "
            f"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        )

    return plan_text.strip(), agent.messages
