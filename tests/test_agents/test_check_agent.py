"""Tests for src.agents.check_agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agents import check_agent


class FakeEnv:
    """Minimal fake for DockerEnvWrapper."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = files or {}

    def execute(self, cmd: str) -> dict:
        if cmd.startswith("cat "):
            path = cmd[4:]
            if path in self.files:
                return {"returncode": 0, "output": self.files[path]}
            return {"returncode": 1, "output": ""}
        return {"returncode": 127, "output": ""}


class TestExtractJsonFromText:
    def test_plain_json(self):
        text = '{"passed": true, "violations": []}'
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": True, "violations": []}

    def test_json_in_markdown_fence(self):
        text = '```json\n{"passed": true, "violations": []}\n```'
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": True, "violations": []}

    def test_json_in_generic_fence(self):
        text = "Some intro\n```\n{\"passed\": true}\n```\nOutro"
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": True}

    def test_text_before_and_after_json(self):
        text = 'Here is result:\n{"passed": false}\nDone!'
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": False}

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object"):
            check_agent._extract_json_from_text("Just plain text.")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            check_agent._extract_json_from_text('{"passed": invalid}')


class TestValidateCheckResult:
    def test_valid_result(self):
        data = {
            "passed": True,
            "violations": [],
            "overall_assessment": "Good plan.",
        }
        result = check_agent._validate_check_result(data)
        assert result["passed"] is True
        assert result["violations"] == []
        assert result["overall_assessment"] == "Good plan."

    def test_with_violations(self):
        data = {
            "passed": False,
            "violations": [
                {"rule": "Rule 1", "reasoning": "Missing test."}
            ],
            "overall_assessment": "Needs work.",
        }
        result = check_agent._validate_check_result(data)
        assert result["passed"] is False
        assert len(result["violations"]) == 1
        assert result["violations"][0]["rule"] == "Rule 1"

    def test_missing_passed_raises(self):
        data = {"violations": [], "overall_assessment": ""}
        with pytest.raises(ValueError, match="'passed' must be a bool"):
            check_agent._validate_check_result(data)

    def test_non_bool_passed_raises(self):
        data = {"passed": "yes", "violations": [], "overall_assessment": ""}
        with pytest.raises(ValueError, match="'passed' must be a bool"):
            check_agent._validate_check_result(data)

    def test_non_list_violations_raises(self):
        data = {"passed": True, "violations": "none", "overall_assessment": ""}
        with pytest.raises(ValueError, match="'violations' must be a list"):
            check_agent._validate_check_result(data)

    def test_filters_empty_rule_violations(self):
        data = {
            "passed": False,
            "violations": [
                {"rule": "", "reasoning": "empty"},
                {"rule": "Valid rule", "reasoning": "reason"},
            ],
            "overall_assessment": "",
        }
        result = check_agent._validate_check_result(data)
        assert len(result["violations"]) == 1
        assert result["violations"][0]["rule"] == "Valid rule"

    def test_non_dict_input_raises(self):
        with pytest.raises(ValueError, match="Expected dict"):
            check_agent._validate_check_result([1, 2, 3])

    def test_missing_optional_fields_use_defaults(self):
        data = {"passed": True}
        result = check_agent._validate_check_result(data)
        assert result["violations"] == []
        assert result["overall_assessment"] == ""


class TestReadCheckResultFromFile:
    def test_reads_file_when_present(self):
        env = FakeEnv({'/tmp/check_result.json': '{"passed": true}'})
        result = check_agent._read_check_result_from_file(env)
        assert result == '{"passed": true}'

    def test_returns_none_when_missing(self):
        env = FakeEnv()
        result = check_agent._read_check_result_from_file(env)
        assert result is None

    def test_returns_none_when_empty(self):
        env = FakeEnv({'/tmp/check_result.json': '   '})
        result = check_agent._read_check_result_from_file(env)
        assert result is None


class TestExtractJsonEdgeCases:
    def test_json_fence_decode_error_falls_back_to_generic_fence(self):
        """Invalid JSON inside ```json fence should fall back to generic fence."""
        text = '```json\n{invalid json}\n```\n```\n{"passed": true}\n```'
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": True}

    def test_generic_fence_decode_error_falls_back_to_raw_json(self):
        """Invalid JSON inside generic fence should fall back to raw JSON."""
        # Use 'not json' (no braces) inside the fence so the greedy raw-json
        # regex does not consume the later valid JSON block.
        text = '```\nnot json\n```\n{"passed": false}'
        result = check_agent._extract_json_from_text(text)
        assert result == {"passed": False}


class TestReadCheckResultEdgeCases:
    def test_returns_none_on_exception(self):
        class BrokenEnv:
            def execute(self, cmd):
                raise RuntimeError("Docker disconnected")

        result = check_agent._read_check_result_from_file(BrokenEnv())
        assert result is None


class TestValidateCheckResultEdgeCases:
    def test_skips_non_dict_violations(self):
        data = {
            "passed": False,
            "violations": [
                "not a dict",
                {"rule": "Valid", "reasoning": "r"},
                123,
            ],
        }
        result = check_agent._validate_check_result(data)
        assert len(result["violations"]) == 1
        assert result["violations"][0]["rule"] == "Valid"


class TestRun:
    @patch("src.agents.check_agent.build_default_agent")
    @patch("src.agents.check_agent.build_model")
    @patch("src.agents.check_agent.import_minisweagent")
    def test_run_reads_result_from_file(self, mock_import, mock_build_model, mock_build_agent):
        mock_DefaultAgent = MagicMock()
        mock_LitellmModel = MagicMock()
        mock_DockerEnv = MagicMock()
        mock_import.return_value = (mock_DefaultAgent, mock_LitellmModel, mock_DockerEnv)

        mock_model = MagicMock()
        mock_build_model.return_value = mock_model

        mock_agent = MagicMock()
        mock_agent.messages = [{"role": "assistant", "content": "check done"}]
        mock_agent.run.return_value = ("Submitted", "submitted text")
        mock_build_agent.return_value = mock_agent

        env = FakeEnv({'/tmp/check_result.json': '{"passed": true, "violations": [], "overall_assessment": "OK"}'})

        from src.config import Config
        config = Config()

        result, traj = check_agent.run(config, "plan text", "issue desc", "rules text", env)

        assert result["passed"] is True
        assert result["violations"] == []
        assert traj == [{"role": "assistant", "content": "check done"}]
        mock_build_agent.assert_called_once()

    @patch("src.agents.check_agent.build_default_agent")
    @patch("src.agents.check_agent.build_model")
    @patch("src.agents.check_agent.import_minisweagent")
    def test_run_fallback_to_exception_msg(self, mock_import, mock_build_model, mock_build_agent):
        """When file read fails, fall back to extracting JSON from agent's final message."""
        mock_DefaultAgent = MagicMock()
        mock_LitellmModel = MagicMock()
        mock_DockerEnv = MagicMock()
        mock_import.return_value = (mock_DefaultAgent, mock_LitellmModel, mock_DockerEnv)

        mock_model = MagicMock()
        mock_build_model.return_value = mock_model

        mock_agent = MagicMock()
        mock_agent.messages = []
        # agent.run returns exception info; file read fails; fallback to exception_msg
        mock_agent.run.return_value = ("Submitted", '{"passed": false, "violations": [{"rule": "R1", "reasoning": "Bad"}], "overall_assessment": "Nope"}')
        mock_build_agent.return_value = mock_agent

        env = FakeEnv()  # no file

        from src.config import Config
        config = Config()

        result, traj = check_agent.run(config, "plan", "issue", "rules", env)

        assert result["passed"] is False
        assert len(result["violations"]) == 1

    @patch("src.agents.check_agent.build_default_agent")
    @patch("src.agents.check_agent.build_model")
    @patch("src.agents.check_agent.import_minisweagent")
    def test_run_invalid_json_raises_taskerror(self, mock_import, mock_build_model, mock_build_agent):
        """When neither file nor exception_msg contains valid JSON, raise TaskError."""
        mock_DefaultAgent = MagicMock()
        mock_LitellmModel = MagicMock()
        mock_DockerEnv = MagicMock()
        mock_import.return_value = (mock_DefaultAgent, mock_LitellmModel, mock_DockerEnv)

        mock_model = MagicMock()
        mock_build_model.return_value = mock_model

        mock_agent = MagicMock()
        mock_agent.messages = []
        mock_agent.run.return_value = ("LimitsExceeded", "some garbage")
        mock_build_agent.return_value = mock_agent

        env = FakeEnv()

        from src.config import Config
        config = Config()

        from src.exceptions import TaskError
        with pytest.raises(TaskError, match="Check agent terminated without a valid result"):
            check_agent.run(config, "plan", "issue", "rules", env)

    def test_extract_result_from_json_text(self):
        text = '{"passed": false, "violations": [{"rule": "R1", "reasoning": "Missing trace."}], "overall_assessment": "Bad"}'
        result = check_agent._extract_json_from_text(text)
        validated = check_agent._validate_check_result(result)
        assert validated["passed"] is False
        assert len(validated["violations"]) == 1
        assert validated["overall_assessment"] == "Bad"

    def test_extract_repairs_invalid_backslash_escape(self):
        text = (
            '{"passed": false, "violations": [], '
            '"overall_assessment": "Inspect C:\\Users\\project and \\d+."}'
        )

        result = check_agent._extract_json_from_text(text)

        assert result["overall_assessment"] == (
            "Inspect C:\\Users\\project and \\d+."
        )

    def test_extract_does_not_repair_other_invalid_json(self):
        text = (
            '{"passed": false, "violations": [], '
            '"overall_assessment": "missing comma" "extra": true}'
        )

        with pytest.raises(ValueError, match="Invalid JSON"):
            check_agent._extract_json_from_text(text)

    def test_extract_from_markdown_fenced_json(self):
        text = '```json\n{"passed": true, "violations": [], "overall_assessment": "OK"}\n```'
        result = check_agent._extract_json_from_text(text)
        validated = check_agent._validate_check_result(result)
        assert validated["passed"] is True
