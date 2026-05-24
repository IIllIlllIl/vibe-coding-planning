"""LLM-based rule quality reviewer agent.

Uses direct LLM calls (LitellmModel.query) instead of DefaultAgent + LocalEnvironment.
Reads files directly on the host, builds a complete prompt with all necessary context,
and sends it to the model in a single API call. This avoids the multi-step interaction
deadlock that occurred when DefaultAgent's action protocol (triple-backtick actions)
conflicted with the FINAL_REVIEW_JSON output format.

Output protocol: the response must contain a FINAL_REVIEW_JSON line:

    FINAL_REVIEW_JSON: {"passed": ..., "score": ..., "feedback": ..., "issues": [...], "improvement_suggestions": ...}
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.agents._deps import build_model, import_minisweagent
from src.analysis.case_loader import CaseDescriptor
from src.config import Config
from src.exceptions import TaskError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM_TEMPLATE = """\
You are a rule quality reviewer. Your task is to evaluate whether an extracted contrastive rule is high-quality and genuinely useful for improving plan-generation agents.

All necessary context (plans, patches, test results, and the rule to review) is provided in the user message below. Evaluate the rule directly — do not request additional files or use tools.

Evaluate the rule on these five criteria (each 0-20 points):

1. FORMAT: Must follow "When [input pattern], [strategy] because [causal justification]."
   - input pattern: a recognizable flaw, situation, or warning sign
   - strategy: what better reasoning should do instead
   - causal justification: explains WHY the strategy works

2. GENERALIZABILITY: The rule must not contain specific filenames, function names, class names, or line numbers. It should be applicable to many different software-engineering tasks.

3. CAUSAL_DEPTH: The "because" clause must provide genuine explanatory depth — not just restate the strategy in different words, but explain the underlying mechanism.

4. ACTIONABILITY: A downstream plan-generation agent reading this rule should know exactly what to do differently in its reasoning process. Vague advice is not actionable.

5. DISTINCTIVENESS: The rule must capture a genuine reasoning difference between the failed plan and the successful plan — not a generic platitude that applies to every debugging task.

After evaluating, output your review result as the LAST line of your response in this EXACT format:

FINAL_REVIEW_JSON: {"passed": true_or_false, "score": 0_to_100, "feedback": "brief summary", "issues": ["issue1", "issue2"], "improvement_suggestions": "how to improve"}

Requirements:
- This MUST be the very last line of your final response
- The JSON must be valid and on a single line
- score must be an integer 0-100
- passed is true if and only if score >= 70
- issues must be a JSON array of strings (empty array if no issues)
- feedback and improvement_suggestions must be strings

Then finish with:
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
"""

# Kept for backward compatibility with existing tests.
REVIEWER_INSTANCE_TEMPLATE = """\
Task instance: {{instance_id}}

Data files available at {{data_base_dir}}/{{instance_id}}/:
{{file_list}}

Round summary (for reference):
{{round_summary}}

Extracted rule to review:
---
{{rule_text}}
---

Instructions:
- Read the plans and trajectories to understand what made the difference between failure and success
- Evaluate whether the extracted rule accurately captures this difference
- Check that the rule is generalizable, actionable, and causally deep
- Output FINAL_REVIEW_JSON as the very last line of your response
"""


# ---------------------------------------------------------------------------
# Helpers (mirroring contrastive_agent conventions)
# ---------------------------------------------------------------------------

def _build_file_list(case: CaseDescriptor) -> str:
    """Build a human-readable file list for the instance prompt."""
    lines: list[str] = []
    for rd in case.rounds:
        lines.append(f"Round {rd.round_num}:")
        lines.append(f"  - {rd.plan_path}")
        lines.append(f"  - {rd.plan_trajectory_path}")
        if rd.code_trajectory_path:
            lines.append(f"  - {rd.code_trajectory_path}")
        lines.append(f"  - {rd.patch_path}")
    lines.append("Shared:")
    lines.append("  - result.json (contains resolved status and test results for all rounds)")
    return "\n".join(lines)


def _build_round_summary(case: CaseDescriptor) -> str:
    """Build a short round summary showing resolved status per round."""
    parts: list[str] = []
    for rd in case.rounds:
        status = "resolved" if rd.resolved else "failed"
        parts.append(f"  Round {rd.round_num}: {status} (generated_by={rd.generated_by})")
    return "\n".join(parts)


def _read_file(path: Path, max_chars: int | None = None) -> str:
    """Safely read a text file, returning a short error string on failure."""
    try:
        content = path.read_text(encoding="utf-8")
        if max_chars is not None and len(content) > max_chars:
            content = content[:max_chars] + f"\n...[truncated at {max_chars} chars]"
        return content
    except Exception as exc:
        return f"<Error reading {path}: {exc}>"


def _load_result_summary(case: CaseDescriptor, data_base_dir: str) -> str:
    """Load result.json and return a minimal summary (excluding large stdout/stderr)."""
    result_file = Path(data_base_dir) / case.instance_id / "result.json"
    if not result_file.exists():
        return "<result.json not found>"

    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"<Error parsing result.json: {exc}>"

    plans_summary: list[dict[str, Any]] = []
    for plan in data.get("plans", []):
        tr = plan.get("test_results", {})
        plans_summary.append({
            "round": plan.get("round"),
            "generated_by": plan.get("generated_by"),
            "test_pass_rate": plan.get("test_pass_rate"),
            "resolved": tr.get("resolved"),
            "error_info": tr.get("error_info"),
        })

    return json.dumps({"plans": plans_summary}, ensure_ascii=False, indent=2)


def _build_review_context(case: CaseDescriptor, data_base_dir: str) -> dict[str, Any]:
    """Read and summarize all files needed for review."""
    base = Path(data_base_dir) / case.instance_id

    context: dict[str, Any] = {
        "instance_id": case.instance_id,
        "round_summary": _build_round_summary(case),
        "result_summary": _load_result_summary(case, data_base_dir),
        "plans": {},
        "patches": {},
    }

    for rd in case.rounds:
        plan_path = Path(rd.plan_path)
        if plan_path.exists():
            context["plans"][f"round_{rd.round_num}"] = _read_file(plan_path)
        else:
            context["plans"][f"round_{rd.round_num}"] = f"<Plan file not found: {plan_path}>"

        patch_path = Path(rd.patch_path)
        if patch_path.exists():
            context["patches"][f"round_{rd.round_num}"] = _read_file(patch_path)
        else:
            context["patches"][f"round_{rd.round_num}"] = f"<Patch file not found: {patch_path}>"

    return context


def _build_user_prompt(case: CaseDescriptor, context: dict[str, Any], rule_text: str) -> str:
    """Build the complete user prompt containing all review context."""
    lines: list[str] = [
        f"Task instance: {case.instance_id}",
        "",
        "## Round Summary",
        context["round_summary"],
        "",
        "## Test Results (summary)",
        context["result_summary"],
        "",
    ]

    for key, content in sorted(context["plans"].items()):
        lines.extend([
            f"## Plan – {key}",
            content,
            "",
        ])

    for key, content in sorted(context["patches"].items()):
        lines.extend([
            f"## Patch – {key}",
            content,
            "",
        ])

    lines.extend([
        "## Extracted Rule to Review",
        "---",
        rule_text,
        "---",
        "",
        "Instructions:",
        "- Evaluate whether the extracted rule accurately captures the reasoning difference between the failed and successful plans shown above.",
        "- Check that the rule is generalizable, actionable, causally deep, and distinctive.",
        "- Output FINAL_REVIEW_JSON as the very last line of your response.",
    ])

    return "\n".join(lines)


def _extract_review_from_text(text: str) -> dict[str, Any] | None:
    """Extract review JSON from a raw text string.

    Tries multiple strategies:
    1. Look for ``FINAL_REVIEW_JSON:`` prefix on the last line
    2. Search for a JSON object containing ``"passed"`` and ``"score"``
    3. Search code blocks for valid JSON
    """
    if not text:
        return None

    # Strategy 1: FINAL_REVIEW_JSON: prefix on any line (prefer last match)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("FINAL_REVIEW_JSON:"):
            json_str = line[len("FINAL_REVIEW_JSON:"):].strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

    # Strategy 2: JSON object containing "passed" and "score"
    patterns = [
        r'\{[^{}]*"passed"\s*:\s*(true|false)\s*,[^{}]*"score"\s*:\s*\d+[^{}]*\}',
        r'\{[^{}]*"score"\s*:\s*\d+\s*,[^{}]*"passed"\s*:\s*(true|false)[^{}]*\}',
        r'\{[^{}]*"passed"[^{}]*"score"[^{}]*\}',
        r'\{[^{}]*"score"[^{}]*"passed"[^{}]*\}',
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

    # Strategy 3: JSON inside code fences
    if "```" in text:
        parts = text.split("```")
        for block in parts[1::2]:
            block = block.strip()
            lines = block.split("\n", 1)
            candidate = lines[1] if len(lines) == 2 else block
            candidate = candidate.strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                try:
                    parsed = json.loads(candidate)
                    if "passed" in parsed and "score" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    pass

    return None


def _extract_review_from_messages(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract review JSON from assistant messages (backward-compatible wrapper).

    Delegates to :func:`_extract_review_from_text` for each assistant message
    (newest-first).
    """
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if not content:
            continue
        review = _extract_review_from_text(content)
        if review is not None:
            return review
    return None


def _normalize_review(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate a review result dict.

    Ensures all expected fields are present with sensible types,
    and recalculates ``passed`` from ``score``.
    """
    normalized: dict[str, Any] = {
        "passed": False,
        "score": 0,
        "feedback": "",
        "issues": [],
        "improvement_suggestions": "",
    }

    if not isinstance(result, dict):
        return normalized

    # score
    try:
        score = int(result.get("score", 0))
        normalized["score"] = max(0, min(100, score))
    except (TypeError, ValueError):
        normalized["score"] = 0

    # passed — recalculate from score for consistency
    if "passed" in result:
        normalized["passed"] = bool(result["passed"]) and normalized["score"] >= 70
    else:
        normalized["passed"] = normalized["score"] >= 70

    # feedback
    fb = result.get("feedback", "")
    normalized["feedback"] = str(fb) if fb else ""

    # issues
    issues = result.get("issues", [])
    if isinstance(issues, list):
        normalized["issues"] = [str(i) for i in issues if i is not None]
    else:
        normalized["issues"] = []

    # improvement_suggestions
    sugg = result.get("improvement_suggestions", "")
    normalized["improvement_suggestions"] = str(sugg) if sugg else ""

    return normalized


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_reviewer(
    config: Config,
    case: CaseDescriptor,
    data_base_dir: str,
    rule_text: str,
    model_name: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the LLM reviewer for a single case using a direct API call.

    Args:
        config: Full configuration object.
        case: CaseDescriptor with per-round file paths.
        data_base_dir: Absolute path to the reflect_success_cases directory.
        rule_text: The extracted rule to review.
        model_name: Optional model override. If None, uses
            ``config.analysis.model``.

    Returns:
        A tuple of (review_result_dict, trajectory_messages).
        The review_result_dict always contains the keys
        ``passed``, ``score``, ``feedback``, ``issues``,
        ``improvement_suggestions``.

    Raises:
        TaskError: If the LLM produces no usable output.
    """
    _, LitellmModel, _ = import_minisweagent()

    analysis_cfg = config.analysis
    effective_model = model_name or analysis_cfg.model
    api_key = config.analysis_api_key or config.api_key

    model = build_model(
        LitellmModel,
        model_name=effective_model,
        api_key=api_key,
        api_base=analysis_cfg.api_base,
    )

    # Build context by reading files directly
    context = _build_review_context(case, data_base_dir)
    user_prompt = _build_user_prompt(case, context, rule_text)

    logger.info(
        "Starting rule review for %s: model=%s rule_length=%d prompt_length=%d",
        case.instance_id,
        effective_model,
        len(rule_text),
        len(user_prompt),
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": REVIEWER_SYSTEM_TEMPLATE},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = model.query(messages)
    except Exception as exc:
        raise TaskError(
            f"LLM query failed for {case.instance_id}: {exc}"
        ) from exc

    content = response.get("content", "")
    if not content:
        raise TaskError(
            f"Reviewer for {case.instance_id} returned empty content."
        )

    # Try to extract review from the response text
    review = _extract_review_from_text(content)
    if review is None:
        # Fallback: try extracting from messages (treating the response as an assistant message)
        review = _extract_review_from_messages(
            [{"role": "assistant", "content": content}]
        )

    if review is None:
        raise TaskError(
            f"Reviewer for {case.instance_id} produced no valid review JSON in response.\n"
            f"Response preview: {content[:500]}"
        )

    review = _normalize_review(review)

    logger.info(
        "[%s] Review complete: score=%d passed=%s",
        case.instance_id,
        review["score"],
        review["passed"],
    )

    # Build a minimal trajectory for audit/debugging
    trajectory = messages + [{"role": "assistant", "content": content}]

    return review, trajectory
