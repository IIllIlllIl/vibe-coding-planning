"""Code generation agent.

Creates a DefaultAgent that generates a Git diff Patch from a Plan.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import build_default_agent, import_minisweagent
from src.config import Config
from src.exceptions import TaskError
from src.prompts.templates import render_code_prompt

logger = logging.getLogger(__name__)

# Markers that indicate a valid Git diff patch
_DIFF_MARKERS = ("diff --git", "--- ", "+++ ")


def _is_valid_patch(text: str) -> bool:
    """Check if the text contains Git diff markers and at least one hunk.

    Requires:
    - At least one diff header marker (diff --git, ---, or +++)
    - At least one hunk marker (@@) indicating actual code changes
    """
    has_header = any(marker in text for marker in _DIFF_MARKERS)
    has_hunk = "@@" in text
    return has_header and has_hunk


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
        env: Docker environment wrapper.

    Returns:
        A tuple of ``(patch_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces empty or non-diff output.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LiteLLMModel = import_minisweagent()

    system_prompt = config.prompts.code_generation_prompt
    user_message = render_code_prompt(system_prompt, plan, issue_description)

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
        "Starting code agent: model=%s max_steps=%s",
        config.system.model,
        config.agent.max_steps,
    )

    patch_text = agent.run(user_message)
    messages = getattr(agent, "messages", [])

    if not patch_text or not patch_text.strip():
        raise TaskError("Code agent produced empty output.")

    if not _is_valid_patch(patch_text):
        raise TaskError(
            "Code agent output does not contain valid Git diff markers. "
            f"Output preview: {patch_text[:200]!r}"
        )

    return patch_text.strip(), messages
