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


class MockDefaultAgent:
    """Simulates a DefaultAgent that successfully submits a patch."""

    last_kwargs: dict = {}

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "diff output"},
        ]

    def run(self, task):
        return (
            "Submitted",
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n+++ b/file.py\n"
            "@@ -1 +1 @@\n-fix\n+fixed\n",
        )


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Simulates step-limit exhaustion — must raise TaskError, no fallback."""

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
            code_generation_prompt="You are a coder.\n\nPlan: {plan}\n\nIssue: {issue_description}",
            code_instance_template="<pr_description>{{task}}</pr_description>",
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
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        patch_text, messages = code_agent.run(
            config, "Plan to fix bug", "Parser fails on input", mock_env
        )

        assert "diff --git" in patch_text
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @patch("src.agents.code_agent.import_minisweagent")
    def test_system_template_passed_to_agent(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(config, "Plan", "Issue", mock_env)
        assert "system_template" in MockDefaultAgent.last_kwargs

    @patch("src.agents.code_agent.import_minisweagent")
    def test_instance_template_passed_to_agent(self, mock_import, config, mock_env):
        """The official SWE-bench instance_template (with submission cmd) must be forwarded."""
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(config, "Plan", "Issue", mock_env)
        assert "instance_template" in MockDefaultAgent.last_kwargs
        assert "{{task}}" in MockDefaultAgent.last_kwargs["instance_template"]

    @patch("src.agents.code_agent.import_minisweagent")
    def test_instance_template_omitted_when_blank(self, mock_import, mock_env):
        """If config provides no instance_template, kwarg is omitted so DefaultAgent uses its default."""
        cfg = Config(
            system=SystemConfig(model="deepseek-v4-flash", api_base="https://api.deepseek.com"),
            prompts=PromptConfig(code_generation_prompt="x"),  # no code_instance_template
            agent=AgentConfig(max_steps=20),
            deepseek_api_key="test-key",
        )
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(cfg, "Plan", "Issue", mock_env)
        assert "instance_template" not in MockDefaultAgent.last_kwargs


class TestRunValidation:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_limits_exceeded_raises_task_error(self, mock_import, config, mock_env):
        """When DefaultAgent hits a limit without submitting, raise TaskError instead of fabricating a patch."""
        mock_import.return_value = (MockDefaultAgentLimitExceeded, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="without a submission"):
            code_agent.run(config, "Plan", "Issue", mock_env)


class TestMissingDependency:
    @patch(
        "src.agents.code_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, mock_env):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            code_agent.run(config, "Plan", "Issue", mock_env)
