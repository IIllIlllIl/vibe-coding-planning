from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.tools import build_swe_chat_stage1_selection as stage1


REVISION = "f" * 40


def _source_manifest() -> dict:
    value = {
        "schema_version": 1,
        "purpose": "swe_chat_frozen_source_manifest",
        "dataset_id": "SALT-NLP/SWE-chat",
        "revision": REVISION,
        "file_count": 3,
        "total_bytes": 0,
        "files": [],
    }
    value["content_sha256"] = stage1.content_sha256(value)
    return value


def _config(source: dict) -> dict:
    return {
        "schema_version": 1,
        "purpose": "swe_chat_stage1_trajectory_selection",
        "selection_id": "test-stage1",
        "source": {
            "dataset_id": "SALT-NLP/SWE-chat",
            "revision": REVISION,
            "source_manifest_sha256": source["content_sha256"],
        },
        "selection": {
            "minimum_agent_percentage_inclusive": 99,
            "explicit_plan_marker": {
                "turn_type": "tool_use",
                "tool_name": "ExitPlanMode",
                "json_field": "plan",
                "require_nonempty_after_strip": True,
            },
        },
    }


def _write_fixture(root: Path) -> None:
    session_ids = ["selected", "empty-plan", "below", "missing", "no-plan"]
    pq.write_table(
        pa.table(
            {
                "session_id": session_ids,
                "repo_id": ["owner/repo"] * 5,
                "agent": ["Claude Code"] * 5,
                "strategy": ["manual-commit"] * 5,
                "agent_percentage": [99.0, 100.0, 98.0, None, 99.0],
                "total_committed": [10.0, 2.0, 3.0, 4.0, 0.0],
                "turn_count": [4.0, 2.0, 2.0, 2.0, 1.0],
            }
        ),
        root / "sessions.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "session_id": session_ids,
                "transcript_path": [
                    f"transcripts/{item}.jsonl" for item in session_ids
                ],
                "context_md": ["context", "", "", "", ""],
            }
        ),
        root / "session_logs.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "session_id": [
                    "selected",
                    "selected",
                    "selected",
                    "empty-plan",
                    "below",
                    "missing",
                    "orphan",
                ],
                "turn_number": [0, 1, 2, 0, 0, 0, 0],
                "turn_type": [
                    "user_prompt",
                    "tool_use",
                    "tool_result",
                    "tool_use",
                    "tool_use",
                    "tool_use",
                    "tool_use",
                ],
                "tool_name": [
                    None,
                    "ExitPlanMode",
                    "ExitPlanMode",
                    "ExitPlanMode",
                    "ExitPlanMode",
                    "ExitPlanMode",
                    "ExitPlanMode",
                ],
                "tool_call_id": [
                    None,
                    "call-1",
                    "call-1",
                    "call-2",
                    "call-3",
                    "call-4",
                    "call-5",
                ],
                "tool_input_json": [
                    None,
                    json.dumps(
                        {"plan": "  inspect, then edit  ", "allowedPrompts": []}
                    ),
                    None,
                    json.dumps({"plan": "   "}),
                    json.dumps({"plan": "below threshold"}),
                    json.dumps({"plan": "missing percentage"}),
                    json.dumps({"plan": "orphan plan"}),
                ],
                "is_continuation": [True, False, False, False, False, False, False],
            }
        ),
        root / "conversations.parquet",
    )


def test_stage1_selection_is_sequential_and_preserves_recovery_pools(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)
    source = _source_manifest()

    manifest = stage1.build_manifest(tmp_path, _config(source), source)

    assert manifest["selection_counts"] == {
        "selected_sessions": 1,
        "excluded_sessions": 4,
        "missing_or_invalid_agent_percentage": 1,
        "agent_percentage_below_threshold": 1,
        "high_agent_without_explicit_nonempty_plan": 2,
        "below_threshold_with_explicit_plan": 1,
        "missing_percentage_with_explicit_plan": 1,
        "selected_explicit_plan_events": 1,
        "malformed_exit_plan_mode_inputs": 0,
        "empty_exit_plan_mode_inputs": 1,
        "orphan_conversation_sessions": 1,
        "orphan_conversation_rows": 1,
        "orphan_sessions_with_explicit_plan": 1,
    }
    selected = manifest["selected_sessions"][0]
    assert selected["session_id"] == "selected"
    assert selected["explicit_nonempty_plan_count"] == 1
    assert selected["plan_events"][0]["turn_number"] == 1
    assert selected["plan_events"][0]["plan_chars"] == 18
    assert "continuation_context" in selected["audit_flags"]
    assert "inspect, then edit" not in json.dumps(manifest)
    assert manifest["excluded_session_ids_by_reason"] == {
        "missing_or_invalid_agent_percentage": ["missing"],
        "agent_percentage_below_threshold": ["below"],
        "high_agent_without_explicit_nonempty_plan": ["empty-plan", "no-plan"],
    }
    assert manifest["source_exclusions"] == {
        "conversation_sessions_missing_sessions_metadata": [
            {
                "session_id": "orphan",
                "conversation_rows": 1,
                "explicit_nonempty_plan_events": 1,
            }
        ]
    }
    assert manifest["content_sha256"] == stage1.content_sha256(manifest)


def test_stage1_rejects_a_changed_marker_policy(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    source = _source_manifest()
    config = _config(source)
    config["selection"]["explicit_plan_marker"]["turn_type"] = "assistant_response"

    try:
        stage1.build_manifest(tmp_path, config, source)
    except ValueError as exc:
        assert "unsupported explicit Plan marker policy" in str(exc)
    else:
        raise AssertionError("changed marker policy was accepted")
