"""Tests for src/agents/code_agent.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.agents import code_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


class MockDefaultAgent:
    def __init__(self, system_prompt: str, model, environment, max_steps: int = 30):
        self.system_prompt = system_prompt
        self.model = model
        self.environment = environment
        self.max_steps = max_steps
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "user msg"},
        ]

    def run(self, user_message: str) -> str:
        return "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-fix\n+fixed"


class MockLiteLLMModel:
    def __init__(self, model: str, api_key: str, api_base: str = ""):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base


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
    return MagicMock()


class TestRunSuccess:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_returns_patch_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        patch, messages = code_agent.run(
            config, "Plan to fix bug", "Parser fails on input", mock_env
        )

        assert "diff --git" in patch
        assert len(messages) == 2

    @patch("src.agents.code_agent.import_minisweagent")
    def test_user_message_contains_plan_and_issue(self, mock_import, config, mock_env):
        captured = {}

        class CapturingAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                captured["user_message"] = user_message
                return super().run(user_message)

        mock_import.return_value = (CapturingAgent, MockLiteLLMModel)
        code_agent.run(config, "Plan content", "Issue description", mock_env)

        assert "Plan content" in captured["user_message"]
        assert "Issue description" in captured["user_message"]

    @patch("src.agents.code_agent.import_minisweagent")
    def test_lite_llm_model_has_api_base(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_environment_passed_to_agent(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_patch_trimmed(self, mock_import, config, mock_env):
        class ValidSpacesAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "  diff --git a/b\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new  "

        mock_import.return_value = (ValidSpacesAgent, MockLiteLLMModel)
        patch, _ = code_agent.run(config, "Plan", "Issue", mock_env)
        assert patch == "diff --git a/b\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new"


class TestRunValidation:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, mock_env):
        class EmptyAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return ""

        mock_import.return_value = (EmptyAgent, MockLiteLLMModel)
        with pytest.raises(TaskError, match="empty"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_non_diff_output_raises_task_error(self, mock_import, config, mock_env):
        class NoDiffAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "This is just plain text without any diff markers."

        mock_import.return_value = (NoDiffAgent, MockLiteLLMModel)
        with pytest.raises(TaskError, match="does not contain valid Git diff"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_header_without_hunk_raises_task_error(self, mock_import, config, mock_env):
        """A patch with ---/+++ but no @@ hunk should be rejected."""
        class HeaderOnlyAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "--- old.py\n+++ new.py\n\nNo actual changes here."

        mock_import.return_value = (HeaderOnlyAgent, MockLiteLLMModel)
        with pytest.raises(TaskError, match="does not contain valid Git diff"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_diff_with_three_dashes_marker_passes(self, mock_import, config, mock_env):
        class DashesAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "--- old.py\n+++ new.py\n@@ -1 +1 @@\n-old\n+new"

        mock_import.return_value = (DashesAgent, MockLiteLLMModel)
        patch, _ = code_agent.run(config, "Plan", "Issue", mock_env)
        assert "--- old.py" in patch

    @patch("src.agents.code_agent.import_minisweagent")
    def test_diff_with_plus_plus_marker_passes(self, mock_import, config, mock_env):
        class PlusPlusAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "+++ new.py\n@@ -1 +1 @@\n-old\n+new"

        mock_import.return_value = (PlusPlusAgent, MockLiteLLMModel)
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
