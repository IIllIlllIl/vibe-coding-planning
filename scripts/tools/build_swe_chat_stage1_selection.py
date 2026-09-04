#!/usr/bin/env python3
"""Build the deterministic SWE-chat Stage-1 trajectory selection manifest."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import yaml


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


def _load_source_contract(
    config_path: Path, source_manifest_override: Path | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported SWE-chat Stage-1 config schema")
    source = config["source"]
    source_path = source_manifest_override
    if source_path is None:
        source_path = Path(source["source_manifest"])
        if not source_path.is_absolute():
            source_path = (config_path.parent.parent / source_path).resolve()
    manifest = json.loads(source_path.read_text(encoding="utf-8"))
    if content_sha256(manifest) != source["source_manifest_sha256"]:
        raise ValueError("SWE-chat source manifest hash mismatch")
    if manifest["dataset_id"] != source["dataset_id"]:
        raise ValueError("SWE-chat dataset ID mismatch")
    if manifest["revision"] != source["revision"]:
        raise ValueError("SWE-chat dataset revision mismatch")
    return config, manifest


def _finite_percentage(value: Any) -> float | None:
    if value is None:
        return None
    percentage = float(value)
    if not math.isfinite(percentage) or not 0 <= percentage <= 100:
        return None
    return percentage


def build_manifest(
    dataset_root: Path,
    config: dict[str, Any],
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    selection = config["selection"]
    threshold = float(selection["minimum_agent_percentage_inclusive"])
    marker = selection["explicit_plan_marker"]
    if not 0 <= threshold <= 100:
        raise ValueError("minimum_agent_percentage_inclusive must be in [0, 100]")
    expected_marker = {
        "turn_type": "tool_use",
        "tool_name": "ExitPlanMode",
        "json_field": "plan",
        "require_nonempty_after_strip": True,
    }
    if marker != expected_marker:
        raise ValueError("unsupported explicit Plan marker policy")

    sessions_path = dataset_root / "sessions.parquet"
    conversations_path = dataset_root / "conversations.parquet"
    logs_path = dataset_root / "session_logs.parquet"
    session_rows = pq.read_table(
        sessions_path,
        columns=[
            "session_id",
            "repo_id",
            "agent",
            "strategy",
            "agent_percentage",
            "total_committed",
            "turn_count",
        ],
    ).to_pylist()
    session_by_id: dict[str, dict[str, Any]] = {}
    for row in session_rows:
        session_id = str(row["session_id"] or "")
        if not session_id or session_id in session_by_id:
            raise ValueError(f"invalid or duplicate session_id {session_id!r}")
        session_by_id[session_id] = row

    log_rows = pq.read_table(
        logs_path,
        columns=["session_id", "transcript_path", "context_md"],
    ).to_pylist()
    logs_by_id: dict[str, dict[str, Any]] = {}
    for row in log_rows:
        session_id = str(row["session_id"] or "")
        if not session_id or session_id in logs_by_id:
            raise ValueError(f"invalid or duplicate session log {session_id!r}")
        logs_by_id[session_id] = row

    plan_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    enter_counts: dict[str, int] = defaultdict(int)
    exit_counts: dict[str, int] = defaultdict(int)
    continuation_sessions: set[str] = set()
    malformed_plan_events = 0
    empty_plan_events = 0
    orphan_conversation_rows: dict[str, int] = defaultdict(int)
    orphan_plan_events: dict[str, int] = defaultdict(int)
    conversation_file = pq.ParquetFile(conversations_path)
    for batch in conversation_file.iter_batches(
        batch_size=100_000,
        columns=[
            "session_id",
            "turn_number",
            "turn_type",
            "tool_name",
            "tool_call_id",
            "tool_input_json",
            "is_continuation",
        ],
    ):
        for row in batch.to_pylist():
            session_id = str(row["session_id"] or "")
            if session_id not in session_by_id:
                orphan_conversation_rows[session_id] += 1
                if (
                    row["turn_type"] == "tool_use"
                    and row["tool_name"] == "ExitPlanMode"
                ):
                    try:
                        orphan_input = json.loads(row["tool_input_json"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        orphan_input = {}
                    orphan_plan = orphan_input.get("plan")
                    if isinstance(orphan_plan, str) and orphan_plan.strip():
                        orphan_plan_events[session_id] += 1
                continue
            if row["is_continuation"]:
                continuation_sessions.add(session_id)
            if row["turn_type"] != "tool_use":
                continue
            if row["tool_name"] == "EnterPlanMode":
                enter_counts[session_id] += 1
                continue
            if row["tool_name"] != "ExitPlanMode":
                continue
            exit_counts[session_id] += 1
            try:
                tool_input = json.loads(row["tool_input_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                malformed_plan_events += 1
                continue
            plan = tool_input.get("plan")
            if not isinstance(plan, str) or not plan.strip():
                empty_plan_events += 1
                continue
            normalized = plan.strip()
            plan_events[session_id].append(
                {
                    "turn_number": int(row["turn_number"]),
                    "tool_call_id": str(row["tool_call_id"] or ""),
                    "plan_chars": len(normalized),
                    "plan_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                    "tool_input_keys": sorted(str(key) for key in tool_input),
                }
            )

    excluded: dict[str, list[str]] = {
        "missing_or_invalid_agent_percentage": [],
        "agent_percentage_below_threshold": [],
        "high_agent_without_explicit_nonempty_plan": [],
    }
    selected_sessions = []
    below_threshold_with_plan = 0
    missing_percentage_with_plan = 0
    for session_id in sorted(session_by_id):
        row = session_by_id[session_id]
        percentage = _finite_percentage(row["agent_percentage"])
        events = sorted(
            plan_events.get(session_id, []),
            key=lambda item: (item["turn_number"], item["tool_call_id"]),
        )
        if percentage is None:
            excluded["missing_or_invalid_agent_percentage"].append(session_id)
            missing_percentage_with_plan += int(bool(events))
            continue
        if percentage < threshold:
            excluded["agent_percentage_below_threshold"].append(session_id)
            below_threshold_with_plan += int(bool(events))
            continue
        if not events:
            excluded["high_agent_without_explicit_nonempty_plan"].append(session_id)
            continue
        log = logs_by_id.get(session_id, {})
        transcript_path = str(log.get("transcript_path") or "")
        context_md = str(log.get("context_md") or "")
        audit_flags = []
        if not transcript_path:
            audit_flags.append("missing_transcript_path")
        if float(row["total_committed"] or 0) <= 0:
            audit_flags.append("nonpositive_total_committed")
        if session_id in continuation_sessions:
            audit_flags.append("continuation_context")
        selected_sessions.append(
            {
                "session_id": session_id,
                "repo_id": str(row["repo_id"]),
                "agent": str(row["agent"]),
                "strategy": str(row["strategy"]),
                "agent_percentage": percentage,
                "total_committed": float(row["total_committed"] or 0),
                "turn_count": None
                if row["turn_count"] is None
                else int(row["turn_count"]),
                "transcript_path": transcript_path or None,
                "context_md_present": bool(context_md),
                "context_md_chars": len(context_md),
                "enter_plan_mode_tool_use_count": enter_counts.get(session_id, 0),
                "exit_plan_mode_tool_use_count": exit_counts.get(session_id, 0),
                "explicit_nonempty_plan_count": len(events),
                "plan_events": events,
                "audit_flags": audit_flags,
            }
        )

    selected_ids = [row["session_id"] for row in selected_sessions]
    excluded_count = sum(len(session_ids) for session_ids in excluded.values())
    if len(selected_ids) + excluded_count != len(session_rows):
        raise AssertionError("Stage-1 session decision universe is incomplete")
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_stage1_high_agent_explicit_plan_selection",
        "selection_id": config["selection_id"],
        "dataset_id": config["source"]["dataset_id"],
        "revision": config["source"]["revision"],
        "source_manifest_sha256": source_manifest["content_sha256"],
        "selection_policy": {
            "unit": "session_trajectory",
            "minimum_agent_percentage_inclusive": threshold,
            "explicit_plan_marker": marker,
            "episode_selection_applied": False,
            "behavioral_label_applied": False,
        },
        "source_counts": {
            "sessions": len(session_rows),
            "conversation_rows": conversation_file.metadata.num_rows,
            "session_logs": len(log_rows),
        },
        "selection_counts": {
            "selected_sessions": len(selected_sessions),
            "excluded_sessions": excluded_count,
            "missing_or_invalid_agent_percentage": len(
                excluded["missing_or_invalid_agent_percentage"]
            ),
            "agent_percentage_below_threshold": len(
                excluded["agent_percentage_below_threshold"]
            ),
            "high_agent_without_explicit_nonempty_plan": len(
                excluded["high_agent_without_explicit_nonempty_plan"]
            ),
            "below_threshold_with_explicit_plan": below_threshold_with_plan,
            "missing_percentage_with_explicit_plan": missing_percentage_with_plan,
            "selected_explicit_plan_events": sum(
                row["explicit_nonempty_plan_count"] for row in selected_sessions
            ),
            "malformed_exit_plan_mode_inputs": malformed_plan_events,
            "empty_exit_plan_mode_inputs": empty_plan_events,
            "orphan_conversation_sessions": len(orphan_conversation_rows),
            "orphan_conversation_rows": sum(orphan_conversation_rows.values()),
            "orphan_sessions_with_explicit_plan": len(orphan_plan_events),
        },
        "selected_session_ids_sha256": hashlib.sha256(
            ("\n".join(selected_ids) + "\n").encode()
        ).hexdigest(),
        "selected_sessions": selected_sessions,
        "excluded_session_ids_by_reason": excluded,
        "source_exclusions": {
            "conversation_sessions_missing_sessions_metadata": [
                {
                    "session_id": session_id or None,
                    "conversation_rows": orphan_conversation_rows[session_id],
                    "explicit_nonempty_plan_events": orphan_plan_events.get(
                        session_id, 0
                    ),
                }
                for session_id in sorted(orphan_conversation_rows)
            ]
        },
    }
    manifest["content_sha256"] = content_sha256(manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config, source_manifest = _load_source_contract(
        config_path,
        None if args.source_manifest is None else args.source_manifest.resolve(),
    )
    manifest = build_manifest(args.dataset_root.resolve(), config, source_manifest)
    atomic_json(args.output.resolve(), manifest)
    print(
        json.dumps(
            {
                "event": "swe_chat_stage1_selection_built",
                "selection_id": manifest["selection_id"],
                "selected_sessions": manifest["selection_counts"]["selected_sessions"],
                "content_sha256": manifest["content_sha256"],
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
