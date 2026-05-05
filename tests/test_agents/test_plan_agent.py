"""Tests for src/agents/plan_agent.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents import plan_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


class MockLiteLLMModel:
    def __init__(self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking


class MockDefaultAgent:
    """Simulates a DefaultAgent that successfully submits a plan."""

    last_kwargs: dict = {}

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "plan output"},
        ]

    def run(self, task):
        return ("Submitted", "plan output")


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "")


class MockDefaultAgentWhitespace(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "   \n\n   ")


class MockDefaultAgentSpaces(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "  plan with spaces  ")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Simulates step-limit exhaustion — falls back to last assistant message."""

    def run(self, task):
        return ("LimitsExceeded", "step limit reached")


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
        ),
        prompts=PromptConfig(
            plan_generation_prompt="You are a planner.",
            plan_format_template="## Analysis\n## Steps",
        ),
        agent=AgentConfig(max_steps=25),
        deepseek_api_key="test-key",
    )


@pytest.fixture
def mock_env():
    return object()


class TestRunSuccess:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_returns_plan_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan, messages = plan_agent.run(config, "Fix parser bug", mock_env)

        assert plan == "plan output"
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_system_template_passed_to_agent(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan_agent.run(config, "Fix parser bug", mock_env)

        # DefaultAgent should receive the rendered system template
        assert "system_template" in MockDefaultAgent.last_kwargs

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_plan_trimmed(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentSpaces, MockLiteLLMModel, object)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "plan with spaces"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_fallback_to_last_assistant(self, mock_import, config, mock_env):
        """When DefaultAgent hits a limit, we fall back to the last assistant message."""
        mock_import.return_value = (MockDefaultAgentLimitExceeded, MockLiteLLMModel, object)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "plan output"


class TestRunValidation:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_empty_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_whitespace_only_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentWhitespace, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)


class TestMissingDependency:
    @patch(
        "src.agents.plan_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, mock_env):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            plan_agent.run(config, "Fix parser bug", mock_env)
