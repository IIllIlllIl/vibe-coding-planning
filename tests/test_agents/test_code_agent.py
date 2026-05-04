"""Tests for src/agents/code_agent.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents import code_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


class MockLiteLLMModel:
    def __init__(self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking

    def query(self, messages: list[dict[str, str]]) -> dict:
        return {
            "content": (
                "diff --git a/file.py b/file.py\n"
                "--- a/file.py\n+++ b/file.py\n"
                "@@ -1 +1 @@\n-fix\n+fixed"
            ),
            "extra": {},
        }


class MockLiteLLMModelEmpty(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "", "extra": {}}


class MockLiteLLMModelNoDiff(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "This is just plain text without any diff markers.", "extra": {}}


class MockLiteLLMModelHeaderOnly(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "--- old.py\n+++ new.py\n\nNo actual changes here.", "extra": {}}


class MockLiteLLMModelDashes(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "--- old.py\n+++ new.py\n@@ -1 +1 @@\n-old\n+new", "extra": {}}


class MockLiteLLMModelPlusPlus(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "+++ new.py\n@@ -1 +1 @@\n-old\n+new", "extra": {}}


class MockLiteLLMModelSpaces(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {
            "content": "  diff --git a/b\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new  ",
            "extra": {},
        }


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
        ),
        prompts=PromptConfig(
            code_generation_prompt="You are a coder.\n\nPlan: {plan}\n\nIssue: {issue_description}",
        ),
        agent=AgentConfig(max_steps=20),
        deepseek_api_key="test-key",
    )


@pytest.fixture
def mock_env():
    return object()


class TestRunSuccess:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_returns_patch_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModel, object)
        patch, messages = code_agent.run(
            config, "Plan to fix bug", "Parser fails on input", mock_env
        )

        assert "diff --git" in patch
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @patch("src.agents.code_agent.import_minisweagent")
    def test_patch_trimmed(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelSpaces, object)
        patch, _ = code_agent.run(config, "Plan", "Issue", mock_env)
        assert patch == "diff --git a/b\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"


class TestRunValidation:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelEmpty, object)
        with pytest.raises(TaskError, match="empty"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_non_diff_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelNoDiff, object)
        with pytest.raises(TaskError, match="does not contain valid Git diff"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_header_without_hunk_raises_task_error(self, mock_import, config, mock_env):
        """A patch with ---/+++ but no @@ hunk should be rejected."""
        mock_import.return_value = (object, MockLiteLLMModelHeaderOnly, object)
        with pytest.raises(TaskError, match="does not contain valid Git diff"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_diff_with_three_dashes_marker_passes(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelDashes, object)
        patch, _ = code_agent.run(config, "Plan", "Issue", mock_env)
        assert "--- old.py" in patch

    @patch("src.agents.code_agent.import_minisweagent")
    def test_diff_with_plus_plus_marker_passes(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelPlusPlus, object)
        patch, _ = code_agent.run(config, "Plan", "Issue", mock_env)
        assert "+++ new.py" in patch


class TestMissingDependency:
    @patch(
        "src.agents.code_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, mock_env):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            code_agent.run(config, "Plan", "Issue", mock_env)
