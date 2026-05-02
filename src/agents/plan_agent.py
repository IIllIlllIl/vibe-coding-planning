"""Plan generation agent.

Creates a DefaultAgent that explores the codebase in a Docker environment
and outputs a natural-language Plan.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import build_default_agent, import_minisweagent
from src.config import Config
from src.exceptions import TaskError
from src.prompts.templates import render_plan_prompt

logger = logging.getLogger(__name__)


def run(
    config: Config,
    issue_description: str,
    env: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the plan generation agent.

    Args:
        config: Full configuration object (includes prompts, model, agent params).
        issue_description: The SWE-bench issue description.
        env: Docker environment wrapper (or compatible execute interface).

    Returns:
        A tuple of ``(plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces an empty or invalid plan.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LiteLLMModel = import_minisweagent()

    system_prompt = render_plan_prompt(
        config.prompts.plan_generation_prompt,
        config.prompts.plan_format_template,
        issue_description,
    )

    model = LiteLLMModel(
        model=config.system.model,
        api_key=config.deepseek_api_key,
        api_base=config.system.api_base,
    )

    # TODO: confirm DefaultAgent accepts `timeout` parameter and pass
    # config.agent.timeout here once mini-swe-agent API is verified.
    agent = build_default_agent(
        DefaultAgent,
        system_prompt=system_prompt,
        model=model,
        environment=env,
        max_steps=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
    )

    logger.info(
        "Starting plan agent: model=%s max_steps=%s",
        config.system.model,
        config.agent.max_steps,
    )

    # Run the agent – DefaultAgent.run() takes a user message
    plan_text = agent.run(issue_description)
    messages = getattr(agent, "messages", [])

    if not plan_text or not plan_text.strip():
        raise TaskError("Plan agent produced empty output.")

    return plan_text.strip(), messages
