from scripts.tools import audit_swe_chat_rq1_feasibility as audit


def _event(turn_type: str, **values):
    return {"turn_type": turn_type, "raw_line_number": 1, "raw_entry_index": 0, "block_index": 0, **values}


def test_primary_bundle_hides_decision_feedback_and_later_plan() -> None:
    case_id = "session-1#first-plan"
    case = {
        "instance_id": case_id,
        "supervision": {"decision": "DO_NOT_ACCEPT"},
        "checker_input": {
            "pre_p1_context": [
                _event("user_prompt", content="Fix the parser"),
                _event("tool_use", tool_name="Read", tool_call_id="pre-read", tool_input={"file_path": "parser.py"}),
                _event("tool_result", tool_call_id="pre-read", content="def parse(): ..."),
            ],
            "proposed_plan_p1": "Change parser.py and run its test.",
            "repository_proxy": {
                "repo": "owner/repo",
                "proxy_commit": "abc",
                "state_semantics": "approximate_pre_session_proxy",
            },
        },
        "reflection_evidence": {
            "decision_result": _event(
                "tool_result",
                tool_call_id="exit",
                content="User rejected tool use. The user said: also update the caller",
            ),
            "subsequent_events": [
                _event(
                    "tool_use",
                    tool_name="Edit",
                    tool_call_id="plan-edit",
                    tool_input={
                        "file_path": "/Users/test/.claude/plans/revised.md",
                        "new_string": "developer-requested revision",
                    },
                ),
                _event("tool_result", tool_call_id="plan-edit", content="updated"),
                _event(
                    "tool_use",
                    tool_name="ExitPlanMode",
                    tool_call_id="p2",
                    tool_input={"plan": "Changed P2"},
                ),
                _event(
                    "tool_use",
                    tool_name="Edit",
                    tool_call_id="edit",
                    tool_input={"file_path": "caller.py", "old_string": "a", "new_string": "b"},
                ),
                _event("tool_result", tool_call_id="edit", content="updated"),
                _event(
                    "tool_use",
                    tool_name="Bash",
                    tool_call_id="test",
                    tool_input={"command": "pytest tests/test_parser.py"},
                ),
                _event("tool_result", tool_call_id="test", content="1 passed"),
                _event("user_prompt", content="Now start a different task"),
                _event(
                    "tool_use",
                    tool_name="Edit",
                    tool_call_id="unrelated-edit",
                    tool_input={"file_path": "unrelated.py"},
                ),
                _event("tool_result", tool_call_id="unrelated-edit", content="updated"),
            ],
        },
    }
    session = {
        "checkpoints_count": 1,
        "files_touched_count": 2,
        "session_success": "92",
    }

    row, bundle = audit._audit_row(case, session, set())

    assert row["developer_feedback_available"] is True
    assert row["later_plan_revision_count"] == 1
    assert row["later_code_change_paired_result_count"] == 2
    assert row["immediate_followup_code_change_paired_result_count"] == 1
    assert row["later_validation_paired_result_count"] == 1
    assert row["objective_task_success_signal_available"] is False
    assert bundle["decision_hidden"] is True
    assert bundle["original_task"]["content"] == "Fix the parser"
    assert len(bundle["pre_p1_developer_prompts"]) == 1
    assert "supervision" not in bundle
    assert "decision_result" not in bundle
    assert all(
        event["tool_name"] != "ExitPlanMode"
        for event in bundle["post_p1_technical_tool_evidence"]
    )
    rendered = audit._canonical_json(bundle)
    assert "User rejected tool use" not in rendered
    assert "Changed P2" not in rendered
    assert "developer-requested revision" not in rendered


def test_validation_command_classifier_is_conservative() -> None:
    assert audit.VALIDATION_RE.search("python -m pytest -q")
    assert audit.VALIDATION_RE.search("npm run build")
    assert not audit.VALIDATION_RE.search("cat package.json")
