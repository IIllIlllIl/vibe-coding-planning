from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.tools import build_swe_chat_stage2_slices as stage2


def _stage1_row(session_id: str, *, continuation: bool = False) -> dict:
    return {
        "session_id": session_id,
        "repo_id": "owner/repo",
        "agent": "Claude Code",
        "strategy": "manual-commit",
        "agent_percentage": 100.0,
        "total_committed": 10.0,
        "turn_count": 5,
        "transcript_path": f"transcripts/{session_id}.jsonl",
        "context_md_present": True,
        "context_md_chars": 100,
        "enter_plan_mode_tool_use_count": 1,
        "exit_plan_mode_tool_use_count": 2,
        "explicit_nonempty_plan_count": 2,
        "plan_events": [
            {
                "turn_number": 5,
                "tool_call_id": f"{session_id}-plan-1",
                "plan_chars": len("first plan"),
                "plan_sha256": __import__("hashlib").sha256(b"first plan").hexdigest(),
                "tool_input_keys": ["plan"],
            },
            {
                "turn_number": 9,
                "tool_call_id": f"{session_id}-plan-2",
                "plan_chars": len("revised plan"),
                "plan_sha256": __import__("hashlib")
                .sha256(b"revised plan")
                .hexdigest(),
                "tool_input_keys": ["plan"],
            },
        ],
        "audit_flags": ["continuation_context"] if continuation else [],
    }


def _row(
    session_id: str,
    turn: int,
    turn_type: str,
    *,
    content: str = "",
    tool_name: str | None = None,
    call_id: str | None = None,
    tool_input: dict | None = None,
    continuation: bool = False,
    first: bool = False,
) -> dict:
    return {
        "session_id": session_id,
        "turn_number": turn,
        "role": {
            "user_prompt": "user",
            "system_injected": "user",
            "assistant_response": "assistant",
            "assistant_thinking": "assistant",
            "tool_use": "tool_use",
            "tool_result": "tool_result",
        }.get(turn_type, "metadata"),
        "turn_type": turn_type,
        "content": content,
        "timestamp": None,
        "is_continuation": continuation,
        "is_first_turn": first,
        "tool_name": tool_name,
        "tool_call_id": call_id,
        "tool_input_json": None if tool_input is None else json.dumps(tool_input),
        "file_path": None,
        "command": None,
        "pattern": None,
        "category": None,
        "bash_category": None,
        "queue_op_subtype": None,
    }


def _rows(session_id: str, *, continuation: bool = False) -> list[dict]:
    return [
        _row(
            session_id,
            0,
            "user_prompt",
            content="fix the parser",
            continuation=continuation,
            first=True,
        ),
        _row(session_id, 1, "assistant_thinking", content="private reasoning"),
        _row(
            session_id,
            2,
            "assistant_response",
            content="Which parser behavior should change?",
        ),
        _row(session_id, 3, "tool_use", tool_name="Read", call_id="read-1"),
        _row(
            session_id,
            4,
            "tool_result",
            content="repository evidence",
            tool_name="Read",
            call_id="read-1",
        ),
        _row(
            session_id,
            5,
            "tool_use",
            tool_name="ExitPlanMode",
            call_id=f"{session_id}-plan-1",
            tool_input={"plan": "first plan"},
        ),
        _row(
            session_id,
            6,
            "tool_result",
            content=(
                "The user doesn't want to proceed with this tool use. "
                "The tool use was rejected."
            ),
            tool_name="ExitPlanMode",
            call_id=f"{session_id}-plan-1",
        ),
        _row(session_id, 7, "user_prompt", content="also cover malformed input"),
        _row(session_id, 8, "assistant_thinking", content="revision reasoning"),
        _row(
            session_id,
            9,
            "tool_use",
            tool_name="ExitPlanMode",
            call_id=f"{session_id}-plan-2",
            tool_input={"plan": "revised plan"},
        ),
        _row(
            session_id,
            10,
            "tool_result",
            content="User has approved your plan. You can now start coding.",
            tool_name="ExitPlanMode",
            call_id=f"{session_id}-plan-2",
        ),
    ]


def _write_conversations(path: Path, rows: list[dict]) -> None:
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    pq.write_table(pa.table(columns), path / "conversations.parquet")


def _write_raw_transcript(path: Path, session_id: str) -> None:
    transcript = path / "transcripts" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    entries = [
        {"type": "user", "message": {"role": "user", "content": "fix the parser"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "private reasoning"},
                    {
                        "type": "text",
                        "text": "Which parser behavior should change?",
                    },
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "read-1",
                        "input": {"file_path": "parser.py"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "read-1",
                        "content": "full untruncated repository evidence",
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "ExitPlanMode",
                        "id": f"{session_id}-plan-1",
                        "input": {"plan": "first plan"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"{session_id}-plan-1",
                        "content": (
                            "The user doesn't want to proceed with this tool use. "
                            "The tool use was rejected."
                        ),
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "also cover malformed input",
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "revision reasoning"},
                    {
                        "type": "tool_use",
                        "name": "ExitPlanMode",
                        "id": f"{session_id}-plan-2",
                        "input": {"plan": "revised plan"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"{session_id}-plan-2",
                        "content": "User has approved your plan. You can now start coding.",
                    }
                ],
            },
        },
    ]
    transcript.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )


def _config() -> dict:
    return {
        "slice_id": "test-stage2",
        "source": {"dataset_id": "SALT-NLP/SWE-chat", "revision": "f" * 40},
        "episode": {
            "unit": "first_plan_per_session",
            "start": "captured_session_start",
            "end": {},
            "checker_visible_turn_types": sorted(stage2.CHECKER_TURN_TYPES),
            "reflection_only_turn_types": sorted(stage2.REFLECTION_TURN_TYPES),
            "excluded_from_projected_context": [],
            "conservative_exclusions": sorted(stage2.CONSERVATIVE_EXCLUSIONS),
        },
    }


def test_first_plan_slice_separates_checker_and_reflection_without_thinking(
    tmp_path: Path,
) -> None:
    selected = _stage1_row("clean")
    _write_conversations(tmp_path, _rows("clean"))
    _write_raw_transcript(tmp_path, "clean")
    stage1_manifest = {"content_sha256": "a" * 64, "selected_sessions": [selected]}

    manifest = stage2.build_slices(
        tmp_path, tmp_path / "output", _config(), stage1_manifest
    )

    case = json.loads((tmp_path / "output/cases/clean.json").read_text())
    assert case["status"] == "eligible"
    assert case["checker_visible"]["proposed_plan"] == "first plan"
    checker = case["checker_visible"]["events"]
    assert checker[-1]["raw_line_number"] == 5
    assert any(
        event.get("content") == "Which parser behavior should change?"
        for event in checker
    )
    assert any(
        event.get("content") == "full untruncated repository evidence"
        for event in checker
    )
    assert all(event["turn_type"] != "assistant_thinking" for event in checker)
    reflection = case["reflection_only"]
    assert reflection["behavior_signal"] == "explicit_rejection"
    assert reflection["decision_result"]["raw_line_number"] == 6
    assert reflection["later_plan_count"] == 1
    assert any(
        event.get("content") == "also cover malformed input"
        for event in reflection["subsequent_events"]
    )
    assert all(
        event["turn_type"] != "assistant_thinking"
        for event in reflection["subsequent_events"]
    )
    assert manifest["counts"]["eligible_cases"] == 1
    assert "first plan" not in json.dumps(manifest)


def test_continuation_is_preserved_but_conservatively_excluded(tmp_path: Path) -> None:
    selected = _stage1_row("continued", continuation=True)
    _write_conversations(tmp_path, _rows("continued", continuation=True))
    _write_raw_transcript(tmp_path, "continued")
    stage1_manifest = {"content_sha256": "a" * 64, "selected_sessions": [selected]}

    stage2.build_slices(tmp_path, tmp_path / "output", _config(), stage1_manifest)

    case = json.loads((tmp_path / "output/cases/continued.json").read_text())
    assert case["status"] == "excluded"
    assert case["exclusion_reasons"] == ["continuation_context"]
    assert case["checker_visible"] is not None
    assert case["reflection_only"] is not None


def test_raw_projection_accepts_concatenated_json_objects(tmp_path: Path) -> None:
    transcript = tmp_path / "concatenated.jsonl"
    first = {"type": "user", "message": {"role": "user", "content": "one"}}
    second = {
        "type": "assistant",
        "message": {"role": "assistant", "content": "two"},
    }
    transcript.write_text(
        json.dumps(first) + json.dumps(second) + "\n", encoding="utf-8"
    )

    events, omitted, line_count, entry_count = stage2.project_raw_transcript(transcript)

    assert line_count == 1
    assert entry_count == 2
    assert [event["content"] for event in events] == ["one", "two"]
    assert [event["raw_entry_index"] for event in events] == [0, 1]
    assert not omitted
