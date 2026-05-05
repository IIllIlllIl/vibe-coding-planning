"""Plan generation agent.

Uses DefaultAgent's interactive step loop so the agent can explore the
codebase (via cat, grep, ls, etc.) before producing the natural-language
Plan text.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    extract_last_assistant,
    import_minisweagent,
)
from src.config import Config
from src.exceptions import TaskError
from src.prompts.templates import render_plan_prompt

logger = logging.getLogger(__name__)


def _extract_result(agent: Any, exception_name: str, exception_msg: str) -> str:
    """Extract the agent's final output based on how it terminated.

    On a clean ``Submitted`` exit, ``exception_msg`` is already the
    submitted text with the ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``
    marker line stripped by ``DefaultAgent.has_finished`` — no extra
    sanitisation is needed. Any other exit (e.g. ``LimitsExceeded``)
    falls back to the last assistant message so a partial plan is still
    surfaced to the pipeline.
    """
    if exception_name == "Submitted":
        return exception_msg.strip()
    return extract_last_assistant(agent.messages)


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
        issue_description: The SWE-bench issue description.
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
        config.prompts.plan_format_template,
        issue_description,
    )

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
        raise TaskError("Plan agent produced empty output.")

    return plan_text.strip(), agent.messages
