"""Postprocess extracted rules into the canonical aggregation format."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from src.analysis import contrastive_agent
from src.analysis.case_loader import CaseDescriptor, load_cases
from src.analysis.opencode_agent import _case_files
from src.analysis.opencode_client import run_opencode
from src.config import Config
from src.exceptions import TaskError

logger = logging.getLogger(__name__)


POSTPROCESS_PROMPT_TEMPLATE = """\
You are postprocessing contrastive reasoning rules.

Your task is to repair FORMAT ONLY. Rewrite the candidate rules below into the exact canonical format:
When [input pattern], [strategy] because [causal justification].

You are given the same case materials that were available during extraction. Use
them only to verify or clarify the causal justification needed after "because".
Do not perform a fresh extraction pass.

Constraints:
- Preserve the original meaning and generality.
- Keep one output rule for each complete candidate rule that can be repaired.
- Do not add task-specific filenames, function names, line numbers, or instance IDs.
- Do not add new rules that are not implied by the candidates.
- If a candidate rule is truncated or lacks enough support to derive a causal
  justification, omit that candidate rather than guessing.
- Output only rule lines.
- Every output line must start with "When " and contain " because ".

Task instance: {instance_id}

Data files available at {data_base_dir}/{instance_id}/:
{file_list}

Round summary:
{round_summary}

Candidate rules:
{candidate_rules}
"""


def is_valid_rule_block(text: str) -> bool:
    """Return True when all rule lines follow the required rule format."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rule_lines = [line for line in lines if line.lower().startswith("when ")]
    return bool(rule_lines) and all(" because " in line.lower() for line in rule_lines)


def _rule_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rule_lines = [line for line in lines if line.lower().startswith("when ")]
    return "\n".join(rule_lines or lines).strip()


def _extract_quoted_value(text: str, key: str) -> str:
    marker = f"{key}="
    start = text.find(marker)
    if start == -1:
        return ""
    pos = start + len(marker)
    if pos >= len(text) or text[pos] not in {"'", '"'}:
        return ""

    quote = text[pos]
    end = pos + 1
    escaped = False
    while end < len(text):
        ch = text[end]
        if ch == "\\" and not escaped:
            escaped = True
            end += 1
            continue
        if ch == quote and not escaped:
            break
        escaped = False
        end += 1

    if end >= len(text):
        return ""

    try:
        return str(ast.literal_eval(text[pos : end + 1]))
    except (SyntaxError, ValueError):
        return ""


def extract_candidate_rule_text(data: dict[str, Any]) -> str:
    """Return the best available rule text from a per-case result.

    Invalid OpenCode outputs are currently stored in the error string as a
    shortened ``stdout=...`` diagnostic, because validation fails before a rule
    is returned to the CLI. Recover that stdout so postprocessing can preserve
    useful Kimi output without mutating the original result file.
    """
    rule = str(data.get("rule", "")).strip()
    if rule:
        return _rule_lines(rule)

    error = str(data.get("error", ""))
    stdout = _extract_quoted_value(error, "stdout")
    if stdout:
        return _rule_lines(stdout)
    return ""


def _build_postprocess_prompt(
    candidate_rules: str,
    *,
    case: CaseDescriptor | None = None,
    data_base_dir: str | Path | None = None,
) -> str:
    if case is None:
        instance_id = "<unknown>"
        file_list = "No case files were provided."
        round_summary = "No round summary was provided."
        data_base = "<unknown>"
    else:
        instance_id = case.instance_id
        file_list = contrastive_agent._build_file_list(case)
        round_summary = contrastive_agent._build_round_summary(case)
        data_base = str(data_base_dir or "")
    return POSTPROCESS_PROMPT_TEMPLATE.format(
        candidate_rules=candidate_rules.strip(),
        instance_id=instance_id,
        data_base_dir=data_base,
        file_list=file_list,
        round_summary=round_summary,
    )


def postprocess_rule_text(
    candidate_rules: str,
    config: Config,
    *,
    cwd: Path | None = None,
    case: CaseDescriptor | None = None,
    data_base_dir: str | Path | None = None,
) -> str:
    """Rewrite candidate rules through OpenCode/Kimi into canonical format."""
    prompt = _build_postprocess_prompt(
        candidate_rules,
        case=case,
        data_base_dir=data_base_dir,
    )
    files = _case_files(case, data_base_dir) if case is not None and data_base_dir else None
    result = run_opencode(
        config=config.analysis,
        prompt=prompt,
        cwd=cwd or Path.cwd(),
        files=files,
    )
    cleaned = _rule_lines(result.stdout)
    if not is_valid_rule_block(cleaned):
        raise TaskError(
            "OpenCode rule postprocess produced invalid rule format: "
            f"stdout={result.stdout[:800]!r} stderr={result.stderr[:800]!r}"
        )
    return cleaned


def _copy_with_metadata(
    data: dict[str, Any],
    source_path: Path,
    output_path: Path,
    *,
    status: str,
    rule: str | None = None,
    rule_valid: bool | None = None,
) -> None:
    new_data = dict(data)
    if rule is not None:
        new_data["rule"] = rule
    if rule_valid is not None:
        new_data["rule_valid"] = rule_valid
    new_data["postprocess"] = {
        "status": status,
        "source_path": str(source_path),
        "original_rule": data.get("rule", ""),
        "original_rule_valid": data.get("rule_valid", False),
        "original_error": data.get("error", ""),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(new_data, indent=2, ensure_ascii=False), encoding="utf-8")


def postprocess_per_case_dir(
    per_case_dir: str | Path,
    output_dir: str | Path,
    config: Config,
    data_base_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Create a postprocessed per-case directory without modifying originals."""
    source_dir = Path(per_case_dir)
    target_dir = Path(output_dir)
    if not source_dir.exists():
        raise FileNotFoundError(f"per_case directory not found: {source_dir}")

    cases_by_id: dict[str, CaseDescriptor] = {}
    if data_base_dir is not None:
        cases_by_id = {case.instance_id: case for case in load_cases(data_base_dir)}

    stats = {
        "source_dir": str(source_dir),
        "output_dir": str(target_dir),
        "data_base_dir": str(data_base_dir) if data_base_dir is not None else "",
        "total": 0,
        "copied_valid": 0,
        "repaired": 0,
        "failed": 0,
        "skipped_empty": 0,
        "failures": [],
    }

    for source_path in sorted(source_dir.glob("*.json")):
        stats["total"] += 1
        output_path = target_dir / source_path.name
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            stats["failed"] += 1
            stats["failures"].append({"file": source_path.name, "error": str(exc)})
            continue

        candidate = extract_candidate_rule_text(data)
        if data.get("rule_valid") is True and is_valid_rule_block(candidate):
            _copy_with_metadata(
                data,
                source_path,
                output_path,
                status="copied_valid",
                rule=candidate,
                rule_valid=True,
            )
            stats["copied_valid"] += 1
            continue

        if not candidate:
            _copy_with_metadata(
                data,
                source_path,
                output_path,
                status="skipped_empty",
                rule="",
                rule_valid=False,
            )
            stats["skipped_empty"] += 1
            continue

        try:
            instance_id = data.get("instance_id", source_path.stem)
            repaired = postprocess_rule_text(
                candidate,
                config,
                case=cases_by_id.get(instance_id),
                data_base_dir=data_base_dir,
            )
        except Exception as exc:
            _copy_with_metadata(
                data,
                source_path,
                output_path,
                status="repair_failed",
                rule=candidate,
                rule_valid=False,
            )
            stats["failed"] += 1
            stats["failures"].append({"file": source_path.name, "error": str(exc)})
            continue

        _copy_with_metadata(
            data,
            source_path,
            output_path,
            status="repaired",
            rule=repaired,
            rule_valid=True,
        )
        stats["repaired"] += 1

    summary_path = target_dir.parent / "postprocess_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Postprocessed rules: total=%d copied=%d repaired=%d failed=%d skipped_empty=%d -> %s",
        stats["total"],
        stats["copied_valid"],
        stats["repaired"],
        stats["failed"],
        stats["skipped_empty"],
        target_dir,
    )
    return stats
