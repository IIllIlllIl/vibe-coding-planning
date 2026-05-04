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
        self.query_calls: list[list[dict]] = []

    def query(self, messages: list[dict[str, str]]) -> dict:
        self.query_calls.append(messages)
        return {"content": "plan output", "extra": {}}


class MockLiteLLMModelEmpty(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        self.query_calls.append(messages)
        return {"content": "", "extra": {}}


class MockLiteLLMModelWhitespace(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        self.query_calls.append(messages)
        return {"content": "   \n\n   ", "extra": {}}


class MockLiteLLMModelSpaces(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        self.query_calls.append(messages)
        return {"content": "  plan with spaces  ", "extra": {}}


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
        mock_import.return_value = (object, MockLiteLLMModel, object)
        plan, messages = plan_agent.run(config, "Fix parser bug", mock_env)

        assert plan == "plan output"
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_system_template_in_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModel, object)
        _, messages = plan_agent.run(config, "Fix parser bug", mock_env)

        # First message is system prompt containing the template
        assert messages[0]["role"] == "system"
        assert "You are a planner" in messages[0]["content"]

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_plan_trimmed(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelSpaces, object)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "plan with spaces"


class TestRunValidation:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_empty_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelEmpty, object)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_whitespace_only_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (object, MockLiteLLMModelWhitespace, object)
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
