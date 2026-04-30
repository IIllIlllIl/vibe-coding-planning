"""Tests for src/output/trajectory.py."""

import json
from pathlib import Path

import pytest

from src.output.trajectory import (
    VALID_ROLES,
    parse_filename,
    save_trajectory,
    validate_filename,
)


class TestSaveTrajectory:
    def test_creates_file_with_correct_name(self, tmp_path: Path):
        messages = [{"role": "user", "content": "hello"}]
        path = save_trajectory(
            messages,
            round_num=1,
            role="plan_gen",
            output_dir=tmp_path,
        )
        assert path.exists()
        assert path.name.startswith("trajectory_1_plan_gen_")
        assert path.name.endswith(".json")

    def test_content_has_required_fields(self, tmp_path: Path):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ]
        path = save_trajectory(
            messages,
            round_num=2,
            role="reflect",
            output_dir=tmp_path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["round"] == 2
        assert data["role"] == "reflect"
        assert "timestamp" in data
        assert data["messages"] == messages

    def test_timestamp_is_iso8601(self, tmp_path: Path):
        messages = [{"role": "user", "content": "test"}]
        path = save_trajectory(
            messages,
            round_num=1,
            role="code_gen",
            output_dir=tmp_path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data["timestamp"]
        # ISO 8601 format: 2026-04-30T10:00:00+00:00
        assert "T" in ts

    def test_extra_metadata_included(self, tmp_path: Path):
        messages = [{"role": "user", "content": "test"}]
        path = save_trajectory(
            messages,
            round_num=1,
            role="plan_gen",
            output_dir=tmp_path,
            extra_metadata={"instance_id": "astropy-1"},
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["instance_id"] == "astropy-1"

    def test_invalid_role_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid role"):
            save_trajectory(
                [],
                round_num=1,
                role="invalid_role",
                output_dir=tmp_path,
            )

    def test_round_zero_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="round_num must be >= 1"):
            save_trajectory(
                [],
                round_num=0,
                role="plan_gen",
                output_dir=tmp_path,
            )

    def test_all_valid_roles(self, tmp_path: Path):
        for role in VALID_ROLES:
            path = save_trajectory(
                [{"role": "user", "content": "test"}],
                round_num=1,
                role=role,
                output_dir=tmp_path,
            )
            assert path.exists()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["role"] == role

    def test_preserves_complex_agent_message_structure(self, tmp_path: Path):
        """Simulates DefaultAgent.messages with tool calls and nested fields."""
        messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant...",
                "timestamp": 1746003000.0,
            },
            {
                "role": "user",
                "content": "Fix the bug in parser",
                "timestamp": 1746003001.0,
            },
            {
                "role": "assistant",
                "content": "I'll search the codebase for the parser module.",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "find /testbed -name parser.py"}',
                        },
                    }
                ],
                "timestamp": 1746003005.0,
            },
            {
                "role": "user",
                "content": "Observation: /testbed/src/parser.py exists",
                "timestamp": 1746003006.0,
            },
        ]
        path = save_trajectory(
            messages,
            round_num=1,
            role="plan_gen",
            output_dir=tmp_path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        saved_messages = data["messages"]
        assert len(saved_messages) == 4
        # Verify tool_calls are preserved
        assistant_msg = saved_messages[2]
        assert "tool_calls" in assistant_msg
        assert assistant_msg["tool_calls"][0]["id"] == "call_123"
        assert assistant_msg["tool_calls"][0]["function"]["name"] == "bash"
        # Verify timestamps are preserved
        assert saved_messages[0]["timestamp"] == 1746003000.0
        assert saved_messages[1]["timestamp"] == 1746003001.0


class TestValidateFilename:
    def test_valid_plan_gen(self):
        assert validate_filename("trajectory_1_plan_gen_20260430T100000.json") is True

    def test_valid_code_gen(self):
        assert validate_filename("trajectory_2_code_gen_20260430T100000.json") is True

    def test_valid_reflect(self):
        assert validate_filename("trajectory_3_reflect_20260430T100000.json") is True

    def test_invalid_role(self):
        assert validate_filename("trajectory_1_unknown_20260430T100000.json") is False

    def test_invalid_timestamp(self):
        assert validate_filename("trajectory_1_plan_gen_2026-04-30T10:00:00.json") is False

    def test_missing_extension(self):
        assert validate_filename("trajectory_1_plan_gen_20260430T100000") is False

    def test_wrong_prefix(self):
        assert validate_filename("other_1_plan_gen_20260430T100000.json") is False


class TestParseFilename:
    def test_parses_valid(self):
        result = parse_filename("trajectory_5_reflect_20260430T143000.json")
        assert result is not None
        assert result["round"] == 5
        assert result["role"] == "reflect"
        assert result["timestamp"] == "20260430T143000"

    def test_parses_plan_gen(self):
        result = parse_filename("trajectory_1_plan_gen_20260430T000000.json")
        assert result["round"] == 1
        assert result["role"] == "plan_gen"

    def test_invalid_returns_none(self):
        assert parse_filename("invalid.json") is None
