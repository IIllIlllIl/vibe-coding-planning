"""Tests for src/agents/plan_agent.py."""

from __future__ import annotations

from typing import Any
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
    last_run_kwargs: dict = {}

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "plan output"},
        ]

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "plan output")


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "")


class MockDefaultAgentWhitespace(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "   \n\n   ")


class MockDefaultAgentSpaces(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "  plan with spaces  ")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Simulates step-limit exhaustion — falls back to last assistant message."""

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("LimitsExceeded", "step limit reached")


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
        ),
        prompts=PromptConfig(
            plan_generation_prompt="You are a planner.\n{{nrpv_block}}",
            plan_instance_template="<pr_description>{{task}}</pr_description>",
            nrpv_block="## Navigation\n## Reproduction\n## Patch\n## Validation",
        ),
        agent=AgentConfig(max_steps=25),
        api_key="test-key",
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
    def test_system_template_passed_verbatim(self, mock_import, config, mock_env):
        """The plan_generation_prompt must be forwarded to DefaultAgent
        unchanged — no host-side str.format / str.replace. The
        ``{{nrpv_block}}`` Jinja placeholder is rendered by
        mini-swe-agent's DefaultAgent at run() time.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan_agent.run(config, "Fix parser bug", mock_env)

        st = MockDefaultAgent.last_kwargs["system_template"]
        # Placeholder preserved verbatim.
        assert "{{nrpv_block}}" in st
        # NRPV content is NOT inlined into the template source.
        assert "## Navigation" not in st

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_nrpv_block_injected_via_agent_run(self, mock_import, config, mock_env):
        """The NRPV definition must flow to mini-swe-agent as a
        ``run(**kwargs)`` value, not as inlined template source. This
        keeps the second-pass Jinja render safe even when NRPV content
        ever ends up containing Jinja-looking fragments."""
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan_agent.run(config, "Fix parser bug", mock_env)

        assert MockDefaultAgent.last_run_kwargs["task"] == "Fix parser bug"
        assert (
            MockDefaultAgent.last_run_kwargs["nrpv_block"]
            == config.prompts.nrpv_block
        )

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_plan_trimmed(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentSpaces, MockLiteLLMModel, object)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "plan with spaces"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_limit_exceeded_raises_task_error(self, mock_import, config, mock_env):
        """When DefaultAgent hits a limit without submitting, raise TaskError."""
        mock_import.return_value = (MockDefaultAgentLimitExceeded, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="terminated without a submission"):
            plan_agent.run(config, "Fix parser bug", mock_env)


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
