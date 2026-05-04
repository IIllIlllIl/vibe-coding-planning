"""Plan generation agent.

Directly queries the LLM with a system + user prompt and returns the
natural-language Plan text.  Does not use DefaultAgent's interactive
step loop because plan generation is a single-shot text-completion task.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents._deps import build_model, import_minisweagent
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
        env: Docker environment wrapper (retained for API compatibility).

    Returns:
        A tuple of ``(plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces an empty or invalid plan.
        FatalError: If mini-swe-agent is not installed.
    """
    _, LitellmModel, _ = import_minisweagent()

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

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_template, "timestamp": time.time()},
        {"role": "user", "content": issue_description, "timestamp": time.time()},
    ]

    logger.info(
        "Starting plan agent: model=%s",
        config.system.model,
    )

    response = model.query(messages)
    plan_text = response.get("content", "")

    messages.append(
        {"role": "assistant", "content": plan_text, "timestamp": time.time()}
    )

    if not plan_text or not plan_text.strip():
        raise TaskError("Plan agent produced empty output.")

    return plan_text.strip(), messages
