"""Load and format aggregated rules for prompt injection.

Rules are stored in ``aggregated_rules.json`` as:

    {
      "always": ["When ..., do ... because ...", ...],
      "branches": [
        {"condition": "...", "rules": ["When ..., do ... because ...", ...]},
        ...
      ]
    }

This module reads the JSON and formats it into a text block suitable for
LLM prompt injection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_aggregated_rules(path: str | Path) -> dict[str, Any]:
    """Load aggregated rules from a JSON file.

    Args:
        path: Path to the ``aggregated_rules.json`` file.

    Returns:
        A dict with ``always`` (list of str) and ``branches`` (list of dict).

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file contains invalid JSON or missing required keys.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Rules file must contain a JSON object, got {type(data).__name__}")

    always = data.get("always", [])
    branches = data.get("branches", [])

    if not isinstance(always, list):
        raise ValueError("'always' must be a list")
    if not isinstance(branches, list):
        raise ValueError("'branches' must be a list")

    # Validate branch structure
    for i, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise ValueError(f"Branch {i} must be a dict")
        if "condition" not in branch:
            raise ValueError(f"Branch {i} missing 'condition' key")
        if "rules" not in branch:
            raise ValueError(f"Branch {i} missing 'rules' key")
        if not isinstance(branch["rules"], list):
            raise ValueError(f"Branch {i} 'rules' must be a list")

    return {"always": always, "branches": branches}


def format_rules_for_prompt(rules: dict[str, Any]) -> str:
    """Format aggregated rules into a prompt-ready text block.

    Universal rules (always) are listed first, then conditional rules
    grouped by branch condition.

    Args:
        rules: Dict with ``always`` and ``branches`` keys.

    Returns:
        A formatted string ready for injection into an LLM prompt.
    """
    lines: list[str] = []

    always_rules = rules.get("always", [])
    if always_rules:
        lines.append("=" * 60)
        lines.append("UNIVERSAL RULES (apply to all plans)")
        lines.append("=" * 60)
        for i, rule in enumerate(always_rules, 1):
            lines.append(f"{i}. {rule}")
        lines.append("")

    branches = rules.get("branches", [])
    if branches:
        lines.append("=" * 60)
        lines.append("CONDITIONAL RULES (apply when the condition matches)")
        lines.append("=" * 60)
        lines.append("")

        for branch in branches:
            condition = branch.get("condition", "")
            branch_rules = branch.get("rules", [])
            if not branch_rules:
                continue

            lines.append(f"--- Branch: {condition} ---")
            for i, rule in enumerate(branch_rules, 1):
                lines.append(f"{i}. {rule}")
            lines.append("")

    return "\n".join(lines)
