"""Reflection agent.

Runs inside a Docker container using DefaultAgent's interactive step loop,
similar to plan_agent. The agent may read files and write temporary test
scripts to verify its understanding, but must not modify source code files.

The system prompt is rendered on the host by combining the current plan,
the assembled feedback text, and the configured reflection prompt template
(``config.prompts.reflection_prompt_template``).  The agent has no access
to trajectory files in the container.
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
from src.prompts import gepa_reflection

logger = logging.getLogger(__name__)


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
    current_plan: str,
    feedback_text: str,
    env: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the reflection agent to generate an improved Plan.

    The agent executes inside the Docker container via DefaultAgent's
    interactive step loop.  All necessary context is pre-loaded into the
    system prompt by rendering ``config.prompts.reflection_prompt_template``
    with ``current_plan`` and ``feedback_text``; the agent has no access
    to trajectory files.

    Args:
        config: Full configuration object.
        current_plan: The plan being optimised (previous round's plan).
        feedback_text: Pre-assembled execution context (original prompt,
            trajectories, test results, patch) built by the pipeline on the
            host.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(new_plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces empty or too-short output.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    system_template = gepa_reflection.render(
        current_plan=current_plan,
        feedback_data=feedback_text,
        template=config.prompts.reflection_prompt_template,
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
        "Starting reflect agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    exception_name, exception_msg = agent.run(task="Improve the plan based on the analysis.")

    # Try to read plan from the file the agent wrote in the container
    plan_text = _read_plan_from_file(env)
    if plan_text is None:
        if exception_name == "Submitted":
            plan_text = exception_msg.strip()
        else:
            plan_text = extract_last_assistant(agent.messages)
        # Extract the plan from ```-fenced block (template instructs the
        # LLM to output inside fences) only when reading from messages;
        # file content is already plain text
        if plan_text:
            plan_text = gepa_reflection.parse_output(plan_text)

    if not plan_text or not plan_text.strip():
        raise TaskError("Reflect agent produced empty output.")

    plan_text = plan_text.strip()
    if len(plan_text) < 50:
        raise TaskError(
            f"Reflect agent output too short ({len(plan_text)} chars). "
            "Expected a detailed plan."
        )

    return plan_text, agent.messages
