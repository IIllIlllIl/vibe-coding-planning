"""Aggregation agent for grouping task-level rules into an input-aware decision tree.

Implements the "Input-Aware Tree Merge" step from ContraPrompt:
- Loads per-case rules from JSON files
- Builds a single prompt with all rules
- Calls an LLM to implicitly cluster rules by condition similarity
- Produces an ``{always: [...], branches: [{condition, rules}]}}`` structure
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates (ContraPrompt-style Input-Aware Tree Merge)
# ---------------------------------------------------------------------------

AGGREGATION_SYSTEM_PROMPT = """\
You are a rule aggregation analyst. Your task is to group a large set of contrastive reasoning rules into an input-aware decision tree.

Each input rule follows this exact format:
  When [input pattern / condition], [strategy] because [causal justification].

Your job is to perform an implicit clustering by semantic similarity of the [input pattern]:

1. Read all rules carefully and understand the [input pattern] of each.
2. Group rules whose [input pattern] describe similar situations, warning signs, or task characteristics.
3. Identify rules whose strategies apply to ALL inputs (universal reasoning patterns). Place these in the "always" group.
4. For each non-always group, derive a concise, observable branch condition that can be checked from the task description or PR description alone.
5. Within each group, merge semantically redundant rules into one concise, generalizable rule.

Requirements:
- Branch conditions must be observable from the input text (e.g., "The task involves regex pattern matching", "The bug report mentions Unicode/internationalization").
- Do NOT include specific filenames, function names, line numbers, repository names, or instance IDs in the output rules.
- Rules must remain fully generalizable beyond any single task.
- The "always" group should only contain strategies that truly apply universally.
- If a group contains multiple rules expressing the same core strategy, merge them into the clearest single formulation.

Output the result as strict JSON with this exact schema:
{
  "always": [
    "When [universal condition], [strategy] because [justification].",
    ...
  ],
  "branches": [
    {
      "condition": "Concise observable condition summarizing this group",
      "rules": [
        "When [condition], [strategy] because [justification].",
        ...
      ]
    },
    ...
  ]
}

Important:
- Output ONLY the JSON object. Do not wrap it in markdown code blocks (no ```json).
- Do not include any explanatory text before or after the JSON.
- Ensure the JSON is valid and parseable.
"""

AGGREGATION_USER_TEMPLATE = """\
You are given {rule_count} contrastive rules extracted from software-engineering bug-fix tasks.

Group these rules into an input-aware decision tree with:
- <always>: for rules that apply to all inputs (universal reasoning strategies).
- <branch condition="...">: for rules that apply only when a specific observable condition is true.

For each branch, derive a concise condition that can be checked from the task description or PR description.

Rules:
{rules_text}

Output ONLY valid JSON. No markdown fences, no extra text.
"""


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

def load_rules(per_case_dir: str | Path) -> list[dict[str, Any]]:
    """Load all valid rules from per-case JSON files.

    Each rule is returned as a dict with keys:
        - text: the rule string
        - instance_id: source case
        - raw_index: index within the source file's rule block
    """
    per_case_path = Path(per_case_dir)
    if not per_case_path.exists():
        raise FileNotFoundError(f"per_case directory not found: {per_case_path}")

    rules: list[dict[str, Any]] = []
    for json_file in sorted(per_case_path.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Skipping unreadable file %s: %s", json_file, exc)
            continue

        if not data.get("rule_valid", False):
            logger.debug("Skipping invalid rule file: %s", json_file.name)
            continue

        instance_id = data.get("instance_id", json_file.stem)
        rule_block = data.get("rule", "")
        if not rule_block or not rule_block.strip():
            continue

        # Split multi-line rules (each line starting with "When ")
        lines = [ln.strip() for ln in rule_block.splitlines() if ln.strip()]
        rule_lines = [ln for ln in lines if ln.lower().startswith("when ")]

        # Fallback: if no "When " lines found, treat the whole block as one rule
        if not rule_lines:
            rule_lines = [" ".join(lines)]

        for idx, line in enumerate(rule_lines):
            if len(line) < 20:
                continue  # too short to be meaningful
            rules.append(
                {
                    "text": line,
                    "instance_id": instance_id,
                    "raw_index": idx,
                }
            )

    logger.info(
        "Loaded %d individual rules from %d case files in %s",
        len(rules),
        len(list(per_case_path.glob("*.json"))),
        per_case_path,
    )
    return rules


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _escape_rule_for_prompt(rule_text: str) -> str:
    """Escape backslashes and braces to avoid Jinja/LaTeX issues in prompts."""
    # Replace literal backslashes with forward slashes (common in regex rules)
    # and strip any markdown formatting
    return rule_text.replace("\\", "/")


def build_user_prompt(rules: list[dict[str, Any]]) -> str:
    """Build the user prompt containing all rules."""
    numbered_rules = []
    for i, rule in enumerate(rules, start=1):
        text = _escape_rule_for_prompt(rule["text"])
        numbered_rules.append(f"{i}. {text}")

    rules_text = "\n".join(numbered_rules)
    return AGGREGATION_USER_TEMPLATE.format(
        rule_count=len(rules),
        rules_text=rules_text,
    )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_litellm(
    model_name: str,
    api_key: str,
    api_base: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 600,
) -> str:
    """Call LLM via litellm.completion."""
    import litellm

    # DeepSeek models need the deepseek/ prefix
    if "/" not in model_name and "deepseek" in api_base:
        model_name = f"deepseek/{model_name}"

    logger.info("Calling LLM for aggregation: model=%s rules_len=%d", model_name, len(user_prompt))

    response = litellm.completion(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        api_key=api_key,
        api_base=api_base,
        timeout=timeout,
    )

    content = response.choices[0].message.content or ""
    logger.info("LLM response received: %d chars", len(content))
    return content


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> dict[str, Any]:
    """Extract the JSON object from LLM response text.

    Handles markdown code blocks and extra text before/after JSON.
    """
    text = text.strip()

    # Try to find JSON inside markdown fences
    if "```" in text:
        # Extract all code blocks and try each
        parts = text.split("```")
        for block in parts[1::2]:
            block = block.strip()
            # Strip language identifier
            lines = block.split("\n", 1)
            candidate = lines[1] if len(lines) == 1 and lines[0] in ("json", "") else block
            if lines[0] in ("json", "", "javascript") and len(lines) == 2:
                candidate = lines[1]
            else:
                candidate = block
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    # Try to find the outermost JSON object via brace matching
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM response")

    # Find matching closing brace
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        raise ValueError("Unclosed JSON object in LLM response")

    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON extracted from LLM response: {exc}") from exc


def _validate_aggregation_result(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the aggregation result."""
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    always = data.get("always", [])
    branches = data.get("branches", [])

    if not isinstance(always, list):
        raise ValueError(f"'always' must be a list, got {type(always).__name__}")
    if not isinstance(branches, list):
        raise ValueError(f"'branches' must be a list, got {type(branches).__name__}")

    # Validate each branch
    for i, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise ValueError(f"Branch {i} must be a dict, got {type(branch).__name__}")
        if "condition" not in branch:
            raise ValueError(f"Branch {i} missing 'condition' field")
        if "rules" not in branch:
            raise ValueError(f"Branch {i} missing 'rules' field")
        if not isinstance(branch["rules"], list):
            raise ValueError(f"Branch {i} 'rules' must be a list")

    # Strip empty always/branch lists for cleanliness
    normalized = {
        "always": [str(r) for r in always if r],
        "branches": [
            {
                "condition": str(b["condition"]),
                "rules": [str(r) for r in b["rules"] if r],
            }
            for b in branches
            if b.get("rules")
        ],
    }

    total_rules = len(normalized["always"]) + sum(
        len(b["rules"]) for b in normalized["branches"]
    )
    logger.info(
        "Aggregation result: %d always rules, %d branches, %d total rules",
        len(normalized["always"]),
        len(normalized["branches"]),
        total_rules,
    )
    return normalized


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def aggregate(
    per_case_dir: str | Path,
    output_path: str | Path,
    model_name: str,
    api_key: str,
    api_base: str,
) -> dict[str, Any]:
    """Aggregate rules from per-case files into a unified rule set.

    Args:
        per_case_dir: Directory containing per_case/*.json files.
        output_path: Path to write the aggregated result JSON.
        model_name: LLM model name (e.g. "deepseek-v4-pro").
        api_key: API key for the LLM provider.
        api_base: Provider base URL.

    Returns:
        The parsed aggregation result dict.
    """
    rules = load_rules(per_case_dir)
    if not rules:
        raise ValueError(f"No valid rules found in {per_case_dir}")

    user_prompt = build_user_prompt(rules)
    raw_response = _call_litellm(
        model_name=model_name,
        api_key=api_key,
        api_base=api_base,
        system_prompt=AGGREGATION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
    )

    parsed = _extract_json_from_text(raw_response)
    result = _validate_aggregation_result(parsed)

    # Add metadata
    result["_meta"] = {
        "source_dir": str(per_case_dir),
        "source_rule_count": len(rules),
        "model": model_name,
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Aggregated rules saved to %s", out_path)

    return result


def aggregate_with_config(
    per_case_dir: str | Path,
    output_path: str | Path,
    config: Config,
    model_override: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper that reads API credentials from Config."""
    analysis_cfg = config.analysis
    model = model_override or analysis_cfg.model
    api_key = config.analysis_api_key
    api_base = analysis_cfg.api_base

    if not api_key:
        raise RuntimeError(
            f"Analysis API key not set (env: {analysis_cfg.api_key_env}). "
            "Cannot run aggregation without API credentials."
        )

    return aggregate(
        per_case_dir=per_case_dir,
        output_path=output_path,
        model_name=model,
        api_key=api_key,
        api_base=api_base,
    )
