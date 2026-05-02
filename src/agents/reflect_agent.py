"""Reflection agent.

Generates an improved Plan from Optimization Feedback using either the GEPA
reflection prompt template or a simplified optimization prompt.

Runs without a Docker environment (uses NullEnvironment for tool calls).
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import build_default_agent, import_minisweagent
from src.config import Config
from src.exceptions import TaskError
from src.prompts import gepa_reflection
from src.prompts.templates import render_plan_prompt

logger = logging.getLogger(__name__)


class NullEnvironment:
    """No-op environment for agents that don't need tool calls.

    DefaultAgent requires an *environment* object.  ``NullEnvironment``
    satisfies the expected interface without performing any real work.
    """

    def execute(self, command: str) -> str:
        """Execute a command – always returns empty string."""
        return ""

    def get_commands(self) -> list[dict[str, Any]]:
        """Return available commands – always returns empty list."""
        return []

    def close(self) -> None:
        """Clean up resources – no-op."""
        pass

    def reset(self) -> None:
        """Reset environment state – no-op."""
        pass

    def __enter__(self) -> "NullEnvironment":
        """Context manager entry."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit – ensures cleanup."""
        self.close()


def run(
    config: Config,
    optimization_feedback: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Run the reflection agent to generate an improved Plan.

    Args:
        config: Full configuration object.
        optimization_feedback: Assembled OptimizationFeedback dict (§4.1).

    Returns:
        A tuple of ``(new_plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces empty or too-short output.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LiteLLMModel = import_minisweagent()

    current_plan = optimization_feedback.get("current_plan", {}).get("content", "")
    feedback_text = _format_feedback(optimization_feedback)

    if config.system.use_gepa_reflection_prompt:
        system_prompt = gepa_reflection.render(
            current_plan=current_plan,
            feedback_data=feedback_text,
            placeholders="",
        )
    else:
        system_prompt = render_plan_prompt(
            config.prompts.plan_optimization_prompt,
            config.prompts.plan_format_template,
        )

    model = LiteLLMModel(
        model=config.system.model,
        api_key=config.deepseek_api_key,
        api_base=config.system.api_base,
    )

    agent = build_default_agent(
        DefaultAgent,
        system_prompt=system_prompt,
        model=model,
        environment=NullEnvironment(),
        max_steps=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
    )

    logger.info("Starting reflect agent: model=%s gepa=%s",
                config.system.model, config.system.use_gepa_reflection_prompt)

    # The user message is the formatted feedback
    plan_text = agent.run(feedback_text)
    messages = getattr(agent, "messages", [])

    if not plan_text or not plan_text.strip():
        raise TaskError("Reflect agent produced empty output.")

    # Parse GEPA output (extract first ``` block) if using GEPA prompt
    if config.system.use_gepa_reflection_prompt:
        plan_text = gepa_reflection.parse_output(plan_text)

    plan_text = plan_text.strip()
    if len(plan_text) < 50:
        raise TaskError(
            f"Reflect agent output too short ({len(plan_text)} chars). "
            "Expected a detailed plan."
        )

    return plan_text, messages


def _format_feedback(feedback: dict[str, Any]) -> str:
    """Format OptimizationFeedback dict into a string for the LLM.

    This is a simplified formatter.  It extracts the key sections that
    the reflection agent needs to analyze.
    """
    parts: list[str] = []

    meta = feedback.get("meta", {})
    parts.append(f"Round: {meta.get('current_round', '?')}")
    parts.append(f"Optimization info level: {meta.get('optimization_info_level', '?')}")

    current_plan = feedback.get("current_plan", {})
    parts.append(f"\nCurrent Plan:\n{current_plan.get('content', '')}")

    test_results = feedback.get("test_results", {})
    if test_results:
        parts.append(f"\nTest Results:\nResolved: {test_results.get('resolved')}")
        stdout = test_results.get("stdout", "")
        if stdout:
            parts.append(f"STDOUT:\n{stdout[:2000]}")  # Truncate if too long
        stderr = test_results.get("stderr", "")
        if stderr:
            parts.append(f"STDERR:\n{stderr[:2000]}")

    error_info = feedback.get("error_info")
    if error_info:
        parts.append(f"\nError Info:\n{error_info}")

    generated_code = feedback.get("generated_code", {})
    patch = generated_code.get("content", "")
    if patch:
        parts.append(f"\nGenerated Patch:\n{patch[:2000]}")

    return "\n".join(parts)
