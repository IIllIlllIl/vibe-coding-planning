"""Plan checker agent.

Validates a generated plan against an aggregated rule set. The agent runs
inside Docker so it can verify concrete references (file paths, function
names, etc.) against the actual codebase when needed.

The agent writes its JSON assessment to ``/tmp/check_result.json`` and
submits with the standard completion marker. The host reads the file and
parses the result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
)
from src.config import Config
from src.exceptions import TaskError

logger = logging.getLogger(__name__)


def _read_check_result_from_file(env: Any) -> str | None:
    """Try to read /tmp/check_result.json from the Docker container.

    Returns:
        The file content if successfully read and non-empty, otherwise None.
    """
    try:
        result = env.execute("cat /tmp/check_result.json")
        if result.get("returncode") == 0:
            content = result.get("output", "").strip()
            if content:
                return content
    except Exception:
        pass
    return None


def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Extract a JSON object from text that may contain markdown fences.

    Tries, in order:
    1. A JSON block inside ```json ... ```
    2. A JSON block inside generic ``` ... ```
    3. A raw JSON object in the text

    Args:
        text: Text that may contain a JSON object.

    Returns:
        The parsed JSON dict.

    Raises:
        ValueError: If no valid JSON object is found.
    """
    # Try markdown fence with json label
    json_fence_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_fence_match:
        try:
            return json.loads(json_fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try generic fence (must not be a language-labelled fence like ```json).
    # Start after the json fence so we don't re-match its closing backticks.
    start_pos = json_fence_match.end() if json_fence_match else 0
    fence_match = re.search(r"```(?!\w)\s*(.+?)\s*```", text[start_pos:], re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try raw JSON object
    json_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

    raise ValueError("No JSON object found in text")


def _validate_check_result(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the check result JSON.

    Ensures the result has the required schema:
    - passed: bool
    - violations: list of dicts with "rule" and "reasoning" keys
    - overall_assessment: str

    Args:
        data: Parsed JSON dict.

    Returns:
        Normalized result dict.

    Raises:
        ValueError: If required fields are missing or have wrong types.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    passed = data.get("passed")
    if not isinstance(passed, bool):
        raise ValueError(f"'passed' must be a bool, got {type(passed).__name__}")

    violations = data.get("violations", [])
    if not isinstance(violations, list):
        raise ValueError(f"'violations' must be a list, got {type(violations).__name__}")

    # Normalize violations
    normalized_violations: list[dict[str, str]] = []
    for i, v in enumerate(violations):
        if not isinstance(v, dict):
            continue
        rule_text = v.get("rule", "").strip()
        reasoning = v.get("reasoning", "").strip()
        if rule_text:  # Only keep violations with non-empty rule text
            normalized_violations.append({"rule": rule_text, "reasoning": reasoning})

    assessment = str(data.get("overall_assessment", "")).strip()

    return {
        "passed": passed,
        "violations": normalized_violations,
        "overall_assessment": assessment,
    }


def run(
    config: Config,
    plan: str,
    issue_description: str,
    rules_text: str,
    env: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the plan checker agent.

    Args:
        config: Full configuration object.
        plan: The plan text to evaluate.
        issue_description: The original SWE-bench issue description.
        rules_text: Formatted rules text for prompt injection.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(check_result, trajectory_messages)``.
        ``check_result`` is a dict with keys ``passed`` (bool),
        ``violations`` (list of dicts), and ``overall_assessment`` (str).

    Raises:
        TaskError: If the agent fails to produce a valid check result.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    system_template = config.prompts.check_prompt
    instance_template = config.prompts.check_instance_template or None

    # Determine model and API credentials for the checker.
    # Checker uses its own dedicated config; no fallback to system.model.
    check_model = config.checker.model
    check_api_base = config.checker.api_base or config.system.api_base
    check_api_key = config.api_key  # Use main API key for checker

    model = build_model(
        LitellmModel,
        model_name=check_model,
        api_key=check_api_key,
        api_base=check_api_base,
    )

    agent = build_default_agent(
        DefaultAgent,
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.checker.max_steps,
        cost_limit=config.checker.cost_limit,
        instance_template=instance_template,
    )

    logger.info(
        "Starting check agent: model=%s step_limit=%s",
        check_model,
        config.checker.max_steps,
    )

    exception_name, exception_msg = agent.run(
        task=issue_description,
        plan=plan,
        rules_text=rules_text,
    )

    # Try to read result from the file the agent wrote in the container
    result_text = _read_check_result_from_file(env)
    if result_text is None:
        # Fallback: try to extract JSON from the agent's final message
        try:
            result_text = _extract_json_from_text(exception_msg)
            if isinstance(result_text, dict):
                result_text = json.dumps(result_text)
        except ValueError:
            result_text = None

    if result_text is None:
        raise TaskError(
            f"Check agent terminated without a valid result (exit_status={exception_name}). "
            f"Expected JSON output in /tmp/check_result.json."
        )

    # Parse and validate the JSON result
    try:
        if isinstance(result_text, str):
            data = _extract_json_from_text(result_text)
        else:
            data = result_text
        check_result = _validate_check_result(data)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Check agent produced invalid JSON: %s. Raw: %s", exc, result_text[:500])
        # Fallback: mark as failed with parsing error
        check_result = {
            "passed": False,
            "violations": [
                {
                    "rule": "JSON parsing failed",
                    "reasoning": f"Could not parse check agent output: {exc}",
                }
            ],
            "overall_assessment": f"Check result parsing failed: {exc}",
            "_parse_error": True,
        }

    return check_result, agent.messages
