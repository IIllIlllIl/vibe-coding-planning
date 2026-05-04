"""Reflection agent.

Directly queries the LLM with a system + user prompt and returns the
improved Plan text.  Does not use DefaultAgent's interactive step loop
because reflection is a single-shot text-completion task.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.agents._deps import build_model, import_minisweagent
from src.config import Config
from src.exceptions import TaskError
from src.prompts import gepa_reflection
from src.prompts.templates import render_plan_prompt

logger = logging.getLogger(__name__)


class NullEnvironment:
    """No-op environment (retained for backward compatibility)."""

    def execute(self, command: str) -> dict[str, Any]:
        """Execute a command – returns empty dict matching 1.17.5 format."""
        return {"output": "", "returncode": 0}

    def get_template_vars(self) -> dict[str, Any]:
        """Return template variables – always returns empty dict."""
        return {}


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
    _, LitellmModel, _ = import_minisweagent()

    current_plan = optimization_feedback.get("current_plan", {}).get("content", "")
    feedback_text = _format_feedback(optimization_feedback)

    if config.system.use_gepa_reflection_prompt:
        system_template = gepa_reflection.render(
            current_plan=current_plan,
            feedback_data=feedback_text,
            placeholders="",
        )
    else:
        system_template = render_plan_prompt(
            config.prompts.plan_optimization_prompt,
            config.prompts.plan_format_template,
        )

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.deepseek_api_key,
        api_base=config.system.api_base,
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_template, "timestamp": time.time()},
        {"role": "user", "content": feedback_text, "timestamp": time.time()},
    ]

    logger.info(
        "Starting reflect agent: model=%s gepa=%s",
        config.system.model,
        config.system.use_gepa_reflection_prompt,
    )

    response = model.query(messages)
    plan_text = response.get("content", "")

    messages.append(
        {"role": "assistant", "content": plan_text, "timestamp": time.time()}
    )

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
