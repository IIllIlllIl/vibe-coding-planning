"""Tests for src/analysis/contrastive_agent.py.

Focus on the instance-isolation fix that prevents stale /tmp/rule.md
from leaking between cases.
"""

from __future__ import annotations

import pytest

from src.analysis.contrastive_agent import (
    CONTRASTIVE_SYSTEM_TEMPLATE,
    _extract_rule_from_messages,
    _read_rule_from_file,
)


class FakeEnv:
    """Minimal fake for LocalEnvironment.execute."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = files or {}

    def execute(self, cmd: str) -> dict:
        # cmd is like "cat /tmp/rule_foo.md"
        if cmd.startswith("cat "):
            path = cmd[4:]
            if path in self.files:
                return {"returncode": 0, "output": self.files[path]}
            return {"returncode": 1, "output": ""}
        if cmd.startswith("rm -f "):
            path = cmd[6:]
            self.files.pop(path, None)
            return {"returncode": 0, "output": ""}
        return {"returncode": 127, "output": ""}


class TestReadRuleFromFile:
    def test_reads_file_when_present(self):
        env = FakeEnv({"/tmp/rule_test.md": "When X, do Y because Z."})
        result = _read_rule_from_file(env, "/tmp/rule_test.md")
        assert result == "When X, do Y because Z."

    def test_returns_none_when_file_missing(self):
        env = FakeEnv()
        result = _read_rule_from_file(env, "/tmp/rule_nonexistent.md")
        assert result is None

    def test_returns_none_when_file_empty(self):
        env = FakeEnv({"/tmp/rule_empty.md": "   "})
        result = _read_rule_from_file(env, "/tmp/rule_empty.md")
        assert result is None

    def test_instance_isolation_different_paths(self):
        """Each instance_id gets a distinct temp file path."""
        env = FakeEnv({
            "/tmp/rule_instance_a.md": "Rule for A",
            "/tmp/rule_instance_b.md": "Rule for B",
        })
        assert _read_rule_from_file(env, "/tmp/rule_instance_a.md") == "Rule for A"
        assert _read_rule_from_file(env, "/tmp/rule_instance_b.md") == "Rule for B"

    def test_instance_isolation_no_cross_contamination(self):
        """Reading one instance's path does not leak another's content."""
        env = FakeEnv({"/tmp/rule_instance_a.md": "Rule for A"})
        assert _read_rule_from_file(env, "/tmp/rule_instance_b.md") is None


class TestExtractRuleFromMessages:
    def test_extracts_from_code_block(self):
        messages = [
            {"role": "assistant", "content": "Here is the rule:\n```\nWhen X, do Y because Z.\n```"}
        ]
        assert _extract_rule_from_messages(messages) == "When X, do Y because Z."

    def test_extracts_from_code_block_with_language(self):
        messages = [
            {"role": "assistant", "content": "```markdown\nWhen X, do Y because Z.\n```"}
        ]
        assert _extract_rule_from_messages(messages) == "When X, do Y because Z."

    def test_extracts_plain_text_when_no_fence(self):
        messages = [
            {"role": "assistant", "content": "When X, do Y because Z."}
        ]
        assert _extract_rule_from_messages(messages) == "When X, do Y because Z."

    def test_prefers_message_with_rule_content(self):
        """Skip messages without 'When ... because ...' and pick the one that has it."""
        messages = [
            {"role": "assistant", "content": "First attempt without rule."},
            {"role": "user", "content": "Try again"},
            {"role": "assistant", "content": "```\nWhen X, do Y because Z.\n```"},
        ]
        assert _extract_rule_from_messages(messages) == "When X, do Y because Z."

    def test_skips_submit_command_and_finds_earlier_rule(self):
        """If the last message is just a submit command, scan earlier messages."""
        messages = [
            {"role": "assistant", "content": "When A, do B because C."},
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        ]
        assert _extract_rule_from_messages(messages) == "When A, do B because C."

    def test_returns_none_when_no_assistant_has_rule(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ]
        assert _extract_rule_from_messages(messages) is None

    def test_returns_none_when_assistant_has_no_rule(self):
        messages = [
            {"role": "assistant", "content": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        ]
        assert _extract_rule_from_messages(messages) is None


class TestSystemTemplate:
    def test_contains_rule_file_path_placeholder(self):
        assert "{{RULE_FILE_PATH}}" in CONTRASTIVE_SYSTEM_TEMPLATE

    def test_contains_submit_command(self):
        assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in CONTRASTIVE_SYSTEM_TEMPLATE

    def test_instance_specific_replacement(self):
        """Simulate the replacement done in run() for each case."""
        instance_id = "django__django-12345"
        rule_path = f"/tmp/rule_{instance_id}.md"
        rendered = CONTRASTIVE_SYSTEM_TEMPLATE.replace("{{RULE_FILE_PATH}}", rule_path)
        assert rule_path in rendered
        assert "{{RULE_FILE_PATH}}" not in rendered


class TestModelFamilyDetection:
    """Tests for _detect_model_family and _build_system_template."""

    def test_detect_kimi_from_api_base(self):
        from src.analysis.contrastive_agent import _detect_model_family
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x", api_base="https://api.kimi.com/coding/", api_key_env="X"
        )
        assert _detect_model_family(cfg) == "kimi"

    def test_detect_deepseek_from_api_base(self):
        from src.analysis.contrastive_agent import _detect_model_family
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x", api_base="https://api.deepseek.com", api_key_env="X"
        )
        assert _detect_model_family(cfg) == "deepseek"

    def test_explicit_family_overrides_auto(self):
        from src.analysis.contrastive_agent import _detect_model_family
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x",
            api_base="https://api.deepseek.com",
            api_key_env="X",
            model_family="kimi",
        )
        assert _detect_model_family(cfg) == "kimi"

    def test_auto_unknown_domain(self):
        from src.analysis.contrastive_agent import _detect_model_family
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x", api_base="https://unknown.example.com", api_key_env="X"
        )
        assert _detect_model_family(cfg) == "unknown"


class TestBuildSystemTemplate:
    """Tests for _build_system_template model-family suffix injection."""

    def test_kimi_suffix_injected(self):
        from src.analysis.contrastive_agent import _build_system_template
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x", api_base="https://api.kimi.com/coding/", api_key_env="X"
        )
        st = _build_system_template(cfg, "/tmp/rule_test.md")
        assert "CRITICAL FORMAT RULE" in st
        assert "exactly ONE bash code block" in st

    def test_deepseek_no_suffix(self):
        from src.analysis.contrastive_agent import _build_system_template
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x", api_base="https://api.deepseek.com", api_key_env="X"
        )
        st = _build_system_template(cfg, "/tmp/rule_test.md")
        assert "CRITICAL FORMAT RULE" not in st

    def test_custom_suffix_overrides_default(self):
        from src.analysis.contrastive_agent import _build_system_template
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x",
            api_base="https://api.kimi.com/coding/",
            api_key_env="X",
            model_family="kimi",
            system_prompt_suffix="[CUSTOM CONSTRAINT]",
        )
        st = _build_system_template(cfg, "/tmp/rule_test.md")
        assert "[CUSTOM CONSTRAINT]" in st
        assert "CRITICAL FORMAT RULE" not in st

    def test_custom_suffix_empty_uses_default(self):
        from src.analysis.contrastive_agent import _build_system_template
        from src.config import AnalysisConfig

        cfg = AnalysisConfig(
            model="x",
            api_base="https://api.kimi.com/coding/",
            api_key_env="X",
            model_family="kimi",
            system_prompt_suffix="",
        )
        st = _build_system_template(cfg, "/tmp/rule_test.md")
        assert "CRITICAL FORMAT RULE" in st
