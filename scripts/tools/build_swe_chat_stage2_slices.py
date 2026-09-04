#!/usr/bin/env python3
"""Build conservative first-Plan slices with a strict information boundary."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml


CHECKER_TURN_TYPES = {
    "system_injected",
    "user_prompt",
    "assistant_response",
    "tool_use",
    "tool_result",
}
REFLECTION_TURN_TYPES = CHECKER_TURN_TYPES | {"summary", "queue_operation"}
ALWAYS_EXCLUDED_TURN_TYPES = {
    "assistant_thinking",
    "system_event",
    "file_snapshot",
    "progress",
}
CONSERVATIVE_EXCLUSIONS = {
    "continuation_context",
    "pre_boundary_summary",
    "missing_transcript_path",
    "nonpositive_total_committed",
    "session_turns_do_not_start_at_zero",
    "missing_first_turn_marker",
    "first_plan_is_not_first_exit_plan_mode",
    "boundary_event_not_unique",
    "decision_result_not_unique",
    "decision_result_not_after_boundary",
    "no_user_prompt_before_boundary",
    "tool_error_behavior_signal",
    "unrecognized_behavior_signal",
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def content_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def load_contract(
    config_path: Path, stage1_override: Path | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported SWE-chat Stage-2 config schema")
    source = config["source"]
    stage1_path = stage1_override
    if stage1_path is None:
        stage1_path = Path(source["stage1_manifest"])
        if not stage1_path.is_absolute():
            stage1_path = (config_path.parent.parent / stage1_path).resolve()
    stage1 = json.loads(stage1_path.read_text(encoding="utf-8"))
    if content_sha256(stage1) != source["stage1_manifest_sha256"]:
        raise ValueError("SWE-chat Stage-1 manifest hash mismatch")
    if stage1["dataset_id"] != source["dataset_id"]:
        raise ValueError("SWE-chat dataset ID mismatch")
    if stage1["revision"] != source["revision"]:
        raise ValueError("SWE-chat revision mismatch")
    episode = config["episode"]
    if episode["unit"] != "first_plan_per_session":
        raise ValueError("unsupported episode unit")
    if episode["start"] != "captured_session_start":
        raise ValueError("unsupported episode start")
    if episode["end"] != {
        "turn_type": "tool_use",
        "tool_name": "ExitPlanMode",
        "plan_source": "tool_input_json.plan",
        "include_boundary_event_in_checker_context": True,
    }:
        raise ValueError("unsupported episode end")
    if set(episode["checker_visible_turn_types"]) != CHECKER_TURN_TYPES:
        raise ValueError("unsupported Checker-visible turn types")
    if set(episode["reflection_only_turn_types"]) != REFLECTION_TURN_TYPES:
        raise ValueError("unsupported Reflection-only turn types")
    if set(episode["excluded_from_projected_context"]) != ALWAYS_EXCLUDED_TURN_TYPES:
        raise ValueError("unsupported projected-context exclusions")
    if set(episode["conservative_exclusions"]) != CONSERVATIVE_EXCLUSIONS:
        raise ValueError("unsupported conservative exclusions")
    return config, stage1


def classify_behavior(text: str) -> str:
    lowered = text.strip().lower()
    if lowered.startswith(
        ("user has approved your plan", "user has approved exiting plan mode")
    ):
        return "explicit_approval"
    if lowered.startswith(("the user doesn", "# plan feedback")):
        return "explicit_rejection"
    if "<tool_use_error>" in lowered:
        return "tool_error"
    return "unrecognized"


def _json_tool_input(raw: Any) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"unparsed_tool_input": str(raw)}


def _behavior_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_behavior_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            if key in value:
                return _behavior_text(value[key])
    return json.dumps(value, sort_keys=True)


def project_raw_transcript(
    transcript_path: Path,
) -> tuple[list[dict[str, Any]], Counter[str], int, int]:
    events = []
    omitted: Counter[str] = Counter()
    raw_line_count = 0
    raw_entry_count = 0
    decoder = json.JSONDecoder()
    with transcript_path.open(encoding="utf-8") as handle:
        for raw_line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw_line_count += 1
            offset = 0
            raw_entry_index = 0
            while offset < len(line):
                while offset < len(line) and line[offset].isspace():
                    offset += 1
                if offset >= len(line):
                    break
                entry, offset = decoder.raw_decode(line, offset)
                raw_entry_count += 1
                if not isinstance(entry, dict):
                    omitted["raw_entry:non_object"] += 1
                    raw_entry_index += 1
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    omitted[f"raw_entry:{entry.get('type')}"] += 1
                    raw_entry_index += 1
                    continue
                role = message.get("role")
                content = message.get("content")
                blocks = (
                    content
                    if isinstance(content, list)
                    else [{"type": "text", "text": content}]
                )
                for block_index, block in enumerate(blocks):
                    if not isinstance(block, dict):
                        omitted["raw_block:non_object"] += 1
                        continue
                    block_type = block.get("type")
                    if block_type == "thinking":
                        omitted["assistant_thinking"] += 1
                        continue
                    base = {
                        "raw_line_number": raw_line_number,
                        "raw_entry_index": raw_entry_index,
                        "block_index": block_index,
                        "role": str(role),
                    }
                    if entry.get("timestamp") is not None:
                        base["timestamp"] = str(entry["timestamp"])
                    if entry.get("permissionMode") is not None:
                        base["permission_mode"] = str(entry["permissionMode"])
                    if block_type == "text":
                        if block.get("text") is None:
                            omitted["raw_block:empty_text"] += 1
                            continue
                        base["turn_type"] = (
                            "assistant_response"
                            if role == "assistant"
                            else "system_injected"
                            if entry.get("isMeta")
                            else "user_prompt"
                        )
                        base["content"] = str(block["text"])
                        events.append(base)
                    elif block_type == "tool_use":
                        base.update(
                            turn_type="tool_use",
                            tool_name=str(block.get("name") or ""),
                            tool_call_id=str(block.get("id") or ""),
                            tool_input=block.get("input") or {},
                        )
                        events.append(base)
                    elif block_type == "tool_result":
                        base.update(
                            turn_type="tool_result",
                            tool_call_id=str(block.get("tool_use_id") or ""),
                            content=block.get("content"),
                        )
                        if block.get("is_error") is not None:
                            base["is_error"] = bool(block["is_error"])
                        events.append(base)
                    else:
                        omitted[f"raw_block:{block_type}"] += 1
                raw_entry_index += 1
    return events, omitted, raw_line_count, raw_entry_count


def project_normalized_metadata(row: dict[str, Any]) -> dict[str, Any]:
    event = {
        "turn_number": int(row["turn_number"]),
        "turn_type": str(row["turn_type"]),
        "content": None if row.get("content") is None else str(row["content"]),
    }
    if row.get("queue_op_subtype") is not None:
        event["queue_op_subtype"] = str(row["queue_op_subtype"])
    if row.get("timestamp") is not None:
        event["timestamp"] = row["timestamp"].isoformat()
    return event


def _minimal_excluded_case(
    stage1_row: dict[str, Any], reasons: list[str]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "case_id": f"{stage1_row['session_id']}#first-plan",
        "status": "excluded",
        "exclusion_reasons": sorted(set(reasons)),
        "selection_provenance": {
            "session_id": stage1_row["session_id"],
            "repo_id": stage1_row["repo_id"],
            "stage1_plan_sha256": stage1_row["plan_events"][0]["plan_sha256"],
        },
        "checker_visible": None,
        "reflection_only": None,
    }


def build_case(
    stage1_row: dict[str, Any], rows: list[dict[str, Any]], transcript_path: Path
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: int(row["turn_number"]))
    reasons = list(stage1_row.get("audit_flags", []))
    if not rows:
        return _minimal_excluded_case(
            stage1_row, reasons + ["boundary_event_not_unique"]
        )
    turn_numbers = [int(row["turn_number"]) for row in rows]
    if len(turn_numbers) != len(set(turn_numbers)):
        reasons.append("boundary_event_not_unique")
    if min(turn_numbers) != 0:
        reasons.append("session_turns_do_not_start_at_zero")
    if not any(row.get("is_first_turn") for row in rows):
        reasons.append("missing_first_turn_marker")

    stage1_plan = stage1_row["plan_events"][0]
    boundary_matches = [
        row
        for row in rows
        if int(row["turn_number"]) == stage1_plan["turn_number"]
        and row["turn_type"] == "tool_use"
        and row["tool_name"] == "ExitPlanMode"
        and str(row.get("tool_call_id") or "") == stage1_plan["tool_call_id"]
    ]
    if len(boundary_matches) != 1:
        return _minimal_excluded_case(
            stage1_row, reasons + ["boundary_event_not_unique"]
        )
    boundary = boundary_matches[0]
    boundary_turn = int(boundary["turn_number"])
    boundary_call_id = str(boundary.get("tool_call_id") or "")
    tool_input = _json_tool_input(boundary.get("tool_input_json"))
    proposed_plan = tool_input.get("plan") if isinstance(tool_input, dict) else None
    normalized_plan = proposed_plan.strip() if isinstance(proposed_plan, str) else ""
    if (
        hashlib.sha256(normalized_plan.encode()).hexdigest()
        != stage1_plan["plan_sha256"]
    ):
        reasons.append("boundary_event_not_unique")

    exit_turns = sorted(
        int(row["turn_number"])
        for row in rows
        if row["turn_type"] == "tool_use" and row["tool_name"] == "ExitPlanMode"
    )
    if not exit_turns or exit_turns[0] != boundary_turn:
        reasons.append("first_plan_is_not_first_exit_plan_mode")
    pre_boundary = [row for row in rows if int(row["turn_number"]) <= boundary_turn]
    if any(row.get("is_continuation") for row in pre_boundary):
        reasons.append("continuation_context")
    if any(row["turn_type"] == "summary" for row in pre_boundary):
        reasons.append("pre_boundary_summary")
    if not any(
        row["turn_type"] == "user_prompt" and int(row["turn_number"]) < boundary_turn
        for row in pre_boundary
    ):
        reasons.append("no_user_prompt_before_boundary")

    normalized_results = [
        row
        for row in rows
        if row["turn_type"] == "tool_result"
        and str(row.get("tool_call_id") or "") == boundary_call_id
    ]
    if len(normalized_results) != 1:
        reasons.append("decision_result_not_unique")
    elif int(normalized_results[0]["turn_number"]) <= boundary_turn:
        reasons.append("decision_result_not_after_boundary")

    if not transcript_path.is_file():
        return _minimal_excluded_case(stage1_row, reasons + ["missing_transcript_path"])
    raw_events, raw_omitted, raw_line_count, raw_entry_count = project_raw_transcript(
        transcript_path
    )
    raw_boundaries = [
        event
        for event in raw_events
        if event["turn_type"] == "tool_use"
        and event.get("tool_name") == "ExitPlanMode"
        and event.get("tool_call_id") == boundary_call_id
    ]
    if len(raw_boundaries) != 1:
        return _minimal_excluded_case(
            stage1_row, reasons + ["boundary_event_not_unique"]
        )
    raw_boundary = raw_boundaries[0]
    raw_boundary_position = (
        raw_boundary["raw_line_number"],
        raw_boundary["raw_entry_index"],
        raw_boundary["block_index"],
    )
    raw_plan = raw_boundary.get("tool_input", {}).get("plan")
    normalized_plan = raw_plan.strip() if isinstance(raw_plan, str) else ""
    if (
        hashlib.sha256(normalized_plan.encode()).hexdigest()
        != stage1_plan["plan_sha256"]
    ):
        reasons.append("boundary_event_not_unique")
    raw_results = [
        event
        for event in raw_events
        if event["turn_type"] == "tool_result"
        and event.get("tool_call_id") == boundary_call_id
    ]
    behavior_signal = "unrecognized"
    decision_result = None
    if len(raw_results) != 1:
        reasons.append("decision_result_not_unique")
    else:
        decision_result = raw_results[0]
        result_position = (
            decision_result["raw_line_number"],
            decision_result["raw_entry_index"],
            decision_result["block_index"],
        )
        if result_position <= raw_boundary_position:
            reasons.append("decision_result_not_after_boundary")
        behavior_signal = classify_behavior(
            _behavior_text(decision_result.get("content"))
        )
        if behavior_signal == "tool_error":
            reasons.append("tool_error_behavior_signal")
        elif behavior_signal == "unrecognized":
            reasons.append("unrecognized_behavior_signal")

    checker_events = [
        event
        for event in raw_events
        if (
            event["raw_line_number"],
            event["raw_entry_index"],
            event["block_index"],
        )
        <= raw_boundary_position
    ]
    result_position = (
        (
            decision_result["raw_line_number"],
            decision_result["raw_entry_index"],
            decision_result["block_index"],
        )
        if decision_result is not None
        else raw_boundary_position
    )
    subsequent_events = [
        event
        for event in raw_events
        if (
            event["raw_line_number"],
            event["raw_entry_index"],
            event["block_index"],
        )
        > result_position
    ]
    normalized_post_metadata = [
        project_normalized_metadata(row)
        for row in rows
        if int(row["turn_number"]) > boundary_turn
        and row["turn_type"] in {"summary", "queue_operation"}
    ]
    later_plans = [
        event
        for event in subsequent_events
        if event["turn_type"] == "tool_use"
        and event.get("tool_name") == "ExitPlanMode"
        and isinstance(event.get("tool_input"), dict)
        and isinstance(event["tool_input"].get("plan"), str)
        and event["tool_input"]["plan"].strip()
    ]
    normalized_omitted_pre = Counter(
        row["turn_type"]
        for row in pre_boundary
        if row["turn_type"] not in CHECKER_TURN_TYPES
    )
    normalized_omitted_post = Counter(
        row["turn_type"]
        for row in rows
        if int(row["turn_number"]) > boundary_turn
        and row["turn_type"] not in REFLECTION_TURN_TYPES
    )
    reasons = sorted(set(reasons) & CONSERVATIVE_EXCLUSIONS)
    case = {
        "schema_version": 1,
        "case_id": f"{stage1_row['session_id']}#first-plan",
        "status": "eligible" if not reasons else "excluded",
        "exclusion_reasons": reasons,
        "selection_provenance": {
            "session_id": stage1_row["session_id"],
            "repo_id": stage1_row["repo_id"],
            "agent": stage1_row["agent"],
            "strategy": stage1_row["strategy"],
            "agent_percentage": stage1_row["agent_percentage"],
            "transcript_path": stage1_row["transcript_path"],
            "context_md_present_but_not_projected": stage1_row["context_md_present"],
        },
        "boundary": {
            "start_turn_number": min(turn_numbers),
            "decision_turn_number": boundary_turn,
            "decision_tool_call_id": boundary_call_id,
            "decision_raw_line_number": raw_boundary["raw_line_number"],
            "decision_raw_entry_index": raw_boundary["raw_entry_index"],
            "decision_raw_block_index": raw_boundary["block_index"],
            "proposed_plan_sha256": stage1_plan["plan_sha256"],
            "proposed_plan_chars": len(normalized_plan),
        },
        "checker_visible": {
            "events": checker_events,
            "proposed_plan": normalized_plan,
        },
        "reflection_only": {
            "behavior_signal": behavior_signal,
            "decision_result": decision_result,
            "subsequent_events": subsequent_events,
            "normalized_post_boundary_metadata": normalized_post_metadata,
            "later_plan_count": len(later_plans),
        },
        "projection_audit": {
            "normalized_source_rows": len(rows),
            "raw_source_lines": raw_line_count,
            "raw_source_entries": raw_entry_count,
            "checker_event_count": len(checker_events),
            "reflection_subsequent_event_count": len(subsequent_events),
            "raw_omitted_by_entry_or_block_type": dict(sorted(raw_omitted.items())),
            "normalized_omitted_pre_boundary_by_turn_type": dict(
                sorted(normalized_omitted_pre.items())
            ),
            "normalized_omitted_post_boundary_by_turn_type": dict(
                sorted(normalized_omitted_post.items())
            ),
            "assistant_thinking_projected": False,
            "context_md_projected": False,
            "tool_results_use_untruncated_raw_transcript": True,
        },
    }
    return case


def build_slices(
    dataset_root: Path,
    output_dir: Path,
    config: dict[str, Any],
    stage1: dict[str, Any],
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Stage-2 output already exists: {output_dir}")
    selected = {row["session_id"]: row for row in stage1["selected_sessions"]}
    columns = [
        "session_id",
        "turn_number",
        "role",
        "turn_type",
        "content",
        "timestamp",
        "is_continuation",
        "is_first_turn",
        "tool_name",
        "tool_call_id",
        "tool_input_json",
        "file_path",
        "command",
        "pattern",
        "category",
        "bash_category",
        "queue_op_subtype",
    ]
    table = pq.read_table(
        dataset_root / "conversations.parquet",
        columns=columns,
        filters=[("session_id", "in", sorted(selected))],
    )
    rows_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in table.to_pylist():
        rows_by_session[str(row["session_id"])].append(row)

    cases_dir = output_dir / "cases"
    cases_dir.mkdir(parents=True)
    records = []
    exclusion_counts: Counter[str] = Counter()
    behavior_counts: Counter[str] = Counter()
    for session_id in sorted(selected):
        transcript = selected[session_id].get("transcript_path")
        transcript_path = (
            dataset_root / str(transcript)
            if transcript
            else dataset_root / "missing-transcript"
        )
        case = build_case(
            selected[session_id],
            rows_by_session.get(session_id, []),
            transcript_path,
        )
        case_bytes = canonical_bytes(case)
        case_path = cases_dir / f"{session_id}.json"
        case_path.write_bytes(case_bytes)
        exclusion_counts.update(case["exclusion_reasons"])
        reflection = case.get("reflection_only") or {}
        behavior = reflection.get("behavior_signal", "unavailable")
        behavior_counts[behavior] += 1
        records.append(
            {
                "case_id": case["case_id"],
                "session_id": session_id,
                "repo_id": selected[session_id]["repo_id"],
                "status": case["status"],
                "exclusion_reasons": case["exclusion_reasons"],
                "behavior_signal": behavior,
                "case_path": str(case_path.relative_to(output_dir)),
                "case_bytes": len(case_bytes),
                "case_sha256": hashlib.sha256(case_bytes).hexdigest(),
                "decision_turn_number": (case.get("boundary") or {}).get(
                    "decision_turn_number"
                ),
            }
        )
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_stage2_first_plan_slices",
        "slice_id": config["slice_id"],
        "dataset_id": config["source"]["dataset_id"],
        "revision": config["source"]["revision"],
        "stage1_manifest_sha256": stage1["content_sha256"],
        "slice_policy": config["episode"],
        "counts": {
            "source_stage1_sessions": len(selected),
            "eligible_cases": sum(row["status"] == "eligible" for row in records),
            "excluded_cases": sum(row["status"] == "excluded" for row in records),
            "behavior_signals": dict(sorted(behavior_counts.items())),
            "exclusion_reasons": dict(sorted(exclusion_counts.items())),
        },
        "cases": records,
    }
    manifest["content_sha256"] = content_sha256(manifest)
    atomic_json(output_dir / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--stage1-manifest", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config, stage1 = load_contract(
        args.config.resolve(),
        None if args.stage1_manifest is None else args.stage1_manifest.resolve(),
    )
    manifest = build_slices(
        args.dataset_root.resolve(), args.output_dir.resolve(), config, stage1
    )
    print(
        json.dumps(
            {
                "event": "swe_chat_stage2_slices_built",
                "slice_id": manifest["slice_id"],
                "counts": manifest["counts"],
                "content_sha256": manifest["content_sha256"],
                "output": str(args.output_dir.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
