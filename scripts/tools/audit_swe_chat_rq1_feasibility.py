#!/usr/bin/env python3
"""Inventory SWE-chat evidence and freeze a decision-hidden RQ1 pilot.

This is an observability audit, not a deficiency classifier.  It reads the
existing Behavioral snapshot so that the accepted Stage-2 first-P1 boundary is
reused byte-for-byte.  The generated primary bundles deliberately omit the
behavioral decision, matched ExitPlanMode result, developer feedback, and later
Plan artifacts.  Decisions are retained only in the audit CSV and a separate
rejoin file used after primary judgments are complete.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pyarrow.parquet as pq


DATASET_ID = "SALT-NLP/SWE-chat"
DATASET_REVISION = "f66cca95b14caaa4177f7ed5eaa424608dadcffa"
DEFAULT_SEED = "swe-chat-rq1-deficiency-pilot-v1-20260904"
READ_TOOLS = {"Read", "Grep", "Glob"}
CHANGE_TOOLS = {"Edit", "Write"}
DIFF_TOOLS = {
    "mcp__conductor__GetWorkspaceDiff",
    "mcp__plugin_context-mode_context-mode__GetWorkspaceDiff",
}
VALIDATION_RE = re.compile(
    r"(?:^|[;&|()]|\s)(?:"
    r"pytest|python\s+-m\s+pytest|unittest|npm\s+(?:run\s+)?(?:test|build)|"
    r"pnpm\s+(?:run\s+)?(?:test|build)|yarn\s+(?:test|build)|vitest|jest|"
    r"go\s+(?:test|build|vet)|cargo\s+(?:test|check|build)|swift\s+test|"
    r"xcodebuild|tsc|mypy|pyright|ruff|eslint|make(?:\s+test)?|"
    r"gradle(?:w)?|mvn|python\s+-m\s+compileall"
    r")(?:\s|$)",
    re.IGNORECASE,
)
REPRO_RE = re.compile(
    r"(?:^|[;&|()]|\s)(?:curl|wget|node|python|ruby|bundle\s+exec|"
    r"go\s+run|cargo\s+run)(?:\s|$)",
    re.IGNORECASE,
)
GIT_EVIDENCE_RE = re.compile(r"\bgit\s+(?:diff|status|show|log)\b", re.IGNORECASE)
CLEAR_COMMAND_RE = re.compile(
    r"<command-name>\s*/clear\s*</command-name>", re.IGNORECASE
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"Expected object at {path}:{line_number}")
                rows.append(value)
    return rows


def _content(event: dict[str, Any]) -> str:
    value = event.get("content", "")
    if isinstance(value, str):
        return value
    return _canonical_json(value) if value is not None else ""


def _command(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("command")
        if isinstance(value, str):
            return value
    value = event.get("command")
    return value if isinstance(value, str) else ""


def _file_path(event: dict[str, Any]) -> str:
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        value = tool_input.get("file_path") or tool_input.get("path")
        if isinstance(value, str):
            return value
    value = event.get("file_path")
    return value if isinstance(value, str) else ""


def _is_plan_artifact(event: dict[str, Any]) -> bool:
    normalized = _file_path(event).replace("\\", "/").casefold()
    return "/.claude/plans/" in normalized


def _rank(seed: str, case_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{case_id}".encode()).hexdigest()


def _technical_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    uses = [event for event in events if event.get("turn_type") == "tool_use"]
    results_by_call: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("turn_type") != "tool_result":
            continue
        call_id = str(event.get("tool_call_id") or "")
        results_by_call.setdefault(call_id, []).append(event)

    reads = [
        event
        for event in uses
        if event.get("tool_name") in READ_TOOLS and not _is_plan_artifact(event)
    ]
    changes = [
        event
        for event in uses
        if event.get("tool_name") in CHANGE_TOOLS and not _is_plan_artifact(event)
    ]
    bash = [event for event in uses if event.get("tool_name") == "Bash"]
    validations = [event for event in bash if VALIDATION_RE.search(_command(event))]
    reproductions = [
        event
        for event in bash
        if event not in validations and REPRO_RE.search(_command(event))
    ]
    git_evidence = [event for event in bash if GIT_EVIDENCE_RE.search(_command(event))]
    git_evidence.extend(event for event in uses if event.get("tool_name") in DIFF_TOOLS)

    def paired(items: list[dict[str, Any]]) -> int:
        return sum(bool(results_by_call.get(str(item.get("tool_call_id") or ""))) for item in items)

    return {
        "uses": uses,
        "results_by_call": results_by_call,
        "reads": reads,
        "changes": changes,
        "validations": validations,
        "reproductions": reproductions,
        "git_evidence": git_evidence,
        "paired_reads": paired(reads),
        "paired_changes": paired(changes),
        "paired_validations": paired(validations),
        "paired_reproductions": paired(reproductions),
        "paired_git_evidence": paired(git_evidence),
    }


def _project_event_with_result(
    event: dict[str, Any], results_by_call: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    call_id = str(event.get("tool_call_id") or "")
    return {
        "source_position": {
            "raw_line_number": event.get("raw_line_number"),
            "raw_entry_index": event.get("raw_entry_index"),
            "block_index": event.get("block_index"),
        },
        "tool_name": event.get("tool_name"),
        "tool_call_id": call_id,
        "tool_input": event.get("tool_input"),
        "tool_results": [
            {
                "source_position": {
                    "raw_line_number": result.get("raw_line_number"),
                    "raw_entry_index": result.get("raw_entry_index"),
                    "block_index": result.get("block_index"),
                },
                "is_error": result.get("is_error"),
                "content": result.get("content"),
            }
            for result in results_by_call.get(call_id, [])
        ],
    }


def _developer_prompts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompts = []
    for event in events:
        if event.get("turn_type") == "user_prompt" and _content(event).strip():
            prompts.append({
                "source_position": {
                    "raw_line_number": event.get("raw_line_number"),
                    "raw_entry_index": event.get("raw_entry_index"),
                    "block_index": event.get("block_index"),
                },
                "content": event.get("content"),
            })
    return prompts


def _original_task(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    prompts = _developer_prompts(events)
    return next(
        (
            prompt
            for prompt in prompts
            if not CLEAR_COMMAND_RE.search(str(prompt["content"]))
        ),
        prompts[0] if prompts else None,
    )


def _checkpoint_metadata(row: dict[str, Any]) -> dict[str, Any]:
    count = row.get("checkpoints_count")
    touched = row.get("files_touched_count")
    return {
        "checkpoint_count": int(count) if count is not None else 0,
        "files_touched_count": int(touched) if touched is not None else 0,
        "session_success_annotation_available": bool(
            str(row.get("session_success") or "").strip()
        ),
        "session_success_annotation_semantics": (
            "LLM-annotated 0-100 session score; not objective task success"
        ),
    }


def _audit_row(
    case: dict[str, Any],
    session: dict[str, Any],
    evidence_verified_ids: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = str(case["instance_id"])
    session_id = case_id.removesuffix("#first-plan")
    checker = case["checker_input"]
    reflection = case["reflection_evidence"]
    pre_events = checker["pre_p1_context"]
    post_events = reflection["subsequent_events"]
    pre = _technical_events(pre_events)
    post = _technical_events(post_events)
    next_prompt_index = next(
        (
            index
            for index, event in enumerate(post_events)
            if event.get("turn_type") == "user_prompt"
        ),
        len(post_events),
    )
    immediate_events = post_events[:next_prompt_index]
    immediate = _technical_events(immediate_events)
    later_plans = [
        event
        for event in post_events
        if event.get("turn_type") == "tool_use"
        and event.get("tool_name") == "ExitPlanMode"
        and isinstance(event.get("tool_input"), dict)
        and str(event["tool_input"].get("plan") or "").strip()
    ]
    task = _original_task(pre_events)
    decision_result = reflection.get("decision_result")
    developer_feedback = (
        case["supervision"]["decision"] == "DO_NOT_ACCEPT"
        and isinstance(decision_result, dict)
        and "user said:" in _content(decision_result).casefold()
    )
    checkpoint = _checkpoint_metadata(session)
    evidence_score = (
        min(immediate["paired_reads"], 8) * 2
        + min(immediate["paired_changes"], 8) * 2
        + min(immediate["paired_validations"], 6) * 3
        + min(immediate["paired_reproductions"], 4) * 2
        + min(immediate["paired_git_evidence"], 4)
        + (2 if checkpoint["checkpoint_count"] else 0)
    )
    if immediate["paired_validations"] and immediate["paired_changes"]:
        retrospective_strength = "STRONG"
    elif evidence_score >= 8:
        retrospective_strength = "MODERATE"
    else:
        retrospective_strength = "WEAK"

    row: dict[str, Any] = {
        "case_id": case_id,
        "session_id": session_id,
        "repo_id": checker["repository_proxy"]["repo"],
        "observed_decision": case["supervision"]["decision"],
        "original_task_available": task is not None,
        "original_task_reliability": "DIRECT_RAW_TRANSCRIPT",
        "original_task_conditioning": "DECISION_NEUTRAL_PRE_P1",
        "pre_p1_conversation_available": bool(pre_events),
        "pre_p1_conversation_event_count": len(pre_events),
        "pre_p1_conversation_reliability": "DIRECT_RAW_TRANSCRIPT_WITH_THINKING_EXCLUDED",
        "pre_p1_conversation_conditioning": "DECISION_NEUTRAL_PRE_P1",
        "pre_p1_repository_observations_available": pre["paired_reads"] > 0,
        "pre_p1_repository_read_count": len(pre["reads"]),
        "pre_p1_repository_paired_result_count": pre["paired_reads"],
        "pre_p1_repository_observations_reliability": "DIRECT_TOOL_CALL_AND_RESULT",
        "pre_p1_repository_observations_conditioning": "DECISION_NEUTRAL_PRE_P1",
        "p1_available": bool(str(checker["proposed_plan_p1"]).strip()),
        "p1_chars": len(str(checker["proposed_plan_p1"])),
        "p1_reliability": "STRUCTURED_EXIT_PLAN_MODE_ARTIFACT",
        "p1_conditioning": "DECISION_NEUTRAL_BOUNDARY",
        "later_repository_reads_available": post["paired_reads"] > 0,
        "later_repository_read_count": len(post["reads"]),
        "later_repository_paired_result_count": post["paired_reads"],
        "later_repository_reads_reliability": "DIRECT_TOOL_CALL_AND_RESULT",
        "later_repository_reads_conditioning": "TECHNICAL_CONTENT_NEUTRAL_BUT_OBSERVABILITY_MAY_BE_DECISION_CONDITIONED",
        "later_code_changes_available": post["paired_changes"] > 0,
        "later_code_change_count": len(post["changes"]),
        "later_code_change_paired_result_count": post["paired_changes"],
        "later_code_changes_reliability": "RECORDED_EDIT_OR_WRITE_CALL_AND_RESULT",
        "later_code_changes_conditioning": "TECHNICAL_CONTENT_NEUTRAL_BUT_OBSERVABILITY_MAY_BE_DECISION_CONDITIONED",
        "later_validation_evidence_available": (
            post["paired_validations"] + post["paired_reproductions"] > 0
        ),
        "later_validation_command_count": len(post["validations"]),
        "later_validation_paired_result_count": post["paired_validations"],
        "later_reproduction_runtime_command_count": len(post["reproductions"]),
        "later_reproduction_runtime_paired_result_count": post["paired_reproductions"],
        "later_validation_evidence_reliability": "DIRECT_COMMAND_AND_RESULT_WHEN_PAIRED",
        "later_validation_evidence_conditioning": "TECHNICAL_CONTENT_NEUTRAL_BUT_OBSERVABILITY_MAY_BE_DECISION_CONDITIONED",
        "checkpoint_commit_diff_evidence_available": bool(
            checkpoint["checkpoint_count"] or post["paired_git_evidence"]
        ),
        "session_checkpoint_count": checkpoint["checkpoint_count"],
        "session_files_touched_count": checkpoint["files_touched_count"],
        "later_git_diff_status_command_count": len(post["git_evidence"]),
        "checkpoint_commit_diff_reliability": "DATASET_METADATA_AND_OPTIONAL_DIRECT_TOOL_RESULT; NO_CANONICAL_FINAL_DIFF",
        "checkpoint_commit_diff_conditioning": "POST_SESSION_OR_POST_DECISION",
        "exact_p1_time_worktree_recoverable": False,
        "evidence_verified_base_candidate_available": case_id in evidence_verified_ids,
        "repository_proxy_available": bool(checker["repository_proxy"].get("proxy_commit")),
        "repository_proxy_semantics": checker["repository_proxy"]["state_semantics"],
        "repository_proxy_reliability": "APPROXIMATE_PRE_SESSION_PROXY; PRE_P1_TRANSCRIPT_OVERRIDES_CONFLICTS",
        "repository_proxy_conditioning": "LABEL_FREE_PRE_SESSION_SELECTION",
        "structured_decision_result_available": isinstance(decision_result, dict),
        "structured_decision_result_reliability": "DIRECT_MATCHED_EXIT_PLAN_MODE_RESULT",
        "structured_decision_result_conditioning": "DECISION_ITSELF; EXCLUDED_FROM_PRIMARY",
        "developer_feedback_available": developer_feedback,
        "developer_feedback_reliability": "DIRECT_TRANSCRIPT_WHEN_PRESENT",
        "developer_feedback_conditioning": "DECISION_CONDITIONED; EXCLUDED_FROM_PRIMARY",
        "later_plan_revision_available": bool(later_plans),
        "later_plan_revision_count": len(later_plans),
        "later_plan_revision_reliability": "STRUCTURED_EXIT_PLAN_MODE_ARTIFACT",
        "later_plan_revision_conditioning": "DECISION_CONDITIONED; EXCLUDED_FROM_PRIMARY",
        "objective_task_success_signal_available": False,
        "objective_task_success_signal_reliability": "UNAVAILABLE",
        "dataset_session_success_annotation_available": checkpoint[
            "session_success_annotation_available"
        ],
        "dataset_session_success_annotation_reliability": "LLM_ANNOTATION_NOT_OBJECTIVE; EXCLUDED",
        "immediate_followup_boundary": "after matched P1 result and before first subsequent user_prompt",
        "immediate_followup_event_count": len(immediate_events),
        "immediate_followup_repository_paired_result_count": immediate["paired_reads"],
        "immediate_followup_code_change_paired_result_count": immediate["paired_changes"],
        "immediate_followup_validation_paired_result_count": immediate["paired_validations"],
        "immediate_followup_reproduction_runtime_paired_result_count": immediate[
            "paired_reproductions"
        ],
        "immediate_followup_git_evidence_paired_result_count": immediate[
            "paired_git_evidence"
        ],
        "immediate_followup_later_plan_count": sum(
            event.get("turn_type") == "tool_use"
            and event.get("tool_name") == "ExitPlanMode"
            and isinstance(event.get("tool_input"), dict)
            and bool(str(event["tool_input"].get("plan") or "").strip())
            for event in immediate_events
        ),
        "retrospective_technical_evidence_strength": retrospective_strength,
        "retrospective_technical_evidence_score": evidence_score,
    }

    primary_tools = []
    for group in (
        immediate["reads"],
        immediate["changes"],
        immediate["validations"],
        immediate["reproductions"],
        immediate["git_evidence"],
    ):
        for event in group:
            projected = _project_event_with_result(event, immediate["results_by_call"])
            key = (projected["tool_call_id"], projected["tool_name"])
            if key not in {(item["tool_call_id"], item["tool_name"]) for item in primary_tools}:
                primary_tools.append(projected)
    bundle = {
        "schema_version": 1,
        "case_id": case_id,
        "repo_id": row["repo_id"],
        "decision_hidden": True,
        "excluded_from_primary": [
            "structured behavioral decision and label",
            "matched ExitPlanMode result",
            "developer feedback requesting Plan changes",
            "later ExitPlanMode Plan artifacts",
            "existing pushback/accept-reject annotations",
            "LLM-annotated session_success score",
        ],
        "observability_caveat": (
            "Post-P1 technical content is direct transcript evidence, but its existence may be "
            "decision-conditioned. The primary window ends before the first new user_prompt; "
            "treat actions alone as insufficient proof of a deficiency."
        ),
        "original_task": task,
        "pre_p1_developer_prompts": _developer_prompts(pre_events),
        "pre_p1_context": pre_events,
        "proposed_plan_p1": checker["proposed_plan_p1"],
        "repository_proxy": checker["repository_proxy"],
        "exact_p1_time_worktree_recoverable": False,
        "evidence_verified_base_candidate_available": case_id in evidence_verified_ids,
        "post_p1_technical_tool_evidence": primary_tools,
        "post_p1_primary_window": {
            "rule": "after matched P1 result and before first subsequent user_prompt",
            "event_count": len(immediate_events),
        },
        "session_checkpoint_metadata": {
            "checkpoint_count": checkpoint["checkpoint_count"],
            "files_touched_count": checkpoint["files_touched_count"],
        },
        "inventory_strength": retrospective_strength,
        "inventory_score_for_pilot_selection_only": evidence_score,
    }
    return row, bundle


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_canonical_json(row) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--snapshot-root", required=True, type=Path)
    parser.add_argument("--sessions-parquet", required=True, type=Path)
    parser.add_argument("--reconstruction-summary", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--pilot-per-decision", type=int, default=10)
    args = parser.parse_args()
    if args.pilot_per_decision <= 0:
        raise SystemExit("--pilot-per-decision must be positive")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    snapshot_files = [args.snapshot_root / "train.jsonl", args.snapshot_root / "validation.jsonl"]
    cases = [case for path in snapshot_files for case in _load_jsonl(path)]
    if len(cases) != 131 or len({case["instance_id"] for case in cases}) != 131:
        raise ValueError("Expected exactly 131 unique Behavioral cases")
    if any(
        case.get("task_semantics") != "behavioral_plan_acceptability_v1"
        for case in cases
    ):
        raise ValueError("Unexpected task semantics")

    sessions = {
        str(row["session_id"]): row
        for row in pq.read_table(
            args.sessions_parquet,
            columns=[
                "session_id",
                "checkpoints_count",
                "files_touched_count",
                "session_success",
            ],
        ).to_pylist()
    }
    reconstruction = _load_json(args.reconstruction_summary)
    verified_ids = {str(item["case_id"]) for item in reconstruction["verified_cases"]}

    audit_rows = []
    bundles: dict[str, dict[str, Any]] = {}
    for case in sorted(cases, key=lambda item: item["instance_id"]):
        case_id = str(case["instance_id"])
        session_id = case_id.removesuffix("#first-plan")
        row, bundle = _audit_row(case, sessions[session_id], verified_ids)
        audit_rows.append(row)
        bundles[case_id] = bundle

    audit_path = output_dir / "rq1_chat_evidence_audit.csv"
    with audit_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(audit_rows[0]))
        writer.writeheader()
        writer.writerows(audit_rows)

    selected: list[dict[str, Any]] = []
    for decision in ("ACCEPT", "DO_NOT_ACCEPT"):
        candidates = [row for row in audit_rows if row["observed_decision"] == decision]
        candidates.sort(
            key=lambda row: (
                -int(row["retrospective_technical_evidence_score"]),
                _rank(args.seed, str(row["case_id"])),
            )
        )
        stratum: list[dict[str, Any]] = []
        per_repo: dict[str, int] = {}
        for row in candidates:
            repo = str(row["repo_id"])
            if per_repo.get(repo, 0) >= 2:
                continue
            stratum.append(row)
            per_repo[repo] = per_repo.get(repo, 0) + 1
            if len(stratum) == args.pilot_per_decision:
                break
        if len(stratum) != args.pilot_per_decision:
            raise ValueError(f"Could not select a repository-diverse {decision} pilot")
        selected.extend(stratum)
    selected.sort(key=lambda row: str(row["case_id"]))
    selected_ids = [str(row["case_id"]) for row in selected]
    primary_bundles = [bundles[case_id] for case_id in selected_ids]
    _write_jsonl(output_dir / "rq1_chat_pilot_primary_bundles.jsonl", primary_bundles)
    _write_json(
        output_dir / "rq1_chat_pilot_decision_rejoin.json",
        {
            "schema_version": 1,
            "warning": "Open only after primary D(P1) judgments are frozen.",
            "decisions": {
                row["case_id"]: row["observed_decision"] for row in selected
            },
        },
    )
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_rq1_plan_deficiency_feasibility_audit",
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "source_snapshot_files": [
            {"path": str(path.resolve()), "sha256": _sha256_file(path)}
            for path in snapshot_files
        ],
        "sessions_parquet": {
            "path": str(args.sessions_parquet.resolve()),
            "sha256": _sha256_file(args.sessions_parquet),
        },
        "reconstruction_summary": {
            "path": str(args.reconstruction_summary.resolve()),
            "sha256": _sha256_file(args.reconstruction_summary),
        },
        "selection_seed": args.seed,
        "pilot_selection_rule": (
            "within each observed decision stratum, descending mechanical technical-evidence "
            "score then SHA-256(seed + NUL + case_id), with at most two cases per repository "
            "per decision stratum; decision hidden from judgments"
        ),
        "pilot_per_decision": args.pilot_per_decision,
        "pilot_case_ids": selected_ids,
        "case_count": len(audit_rows),
        "pilot_count": len(selected_ids),
        "outputs": {
            "audit_csv": audit_path.name,
            "primary_bundles": "rq1_chat_pilot_primary_bundles.jsonl",
            "decision_rejoin": "rq1_chat_pilot_decision_rejoin.json",
        },
    }
    _write_json(output_dir / "audit-manifest.json", manifest)
    print(json.dumps({"cases": len(audit_rows), "pilot": len(selected_ids), "output": str(output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
