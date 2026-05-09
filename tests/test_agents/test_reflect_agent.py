"""Tests for src/agents/reflect_agent.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.agents import reflect_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


# Reflection template stub used by the test suite.  We do NOT depend on the
# YAML-resident template here because the agent's behaviour is independent
# of template wording — render() just needs the four placeholders to be
# present so str.format succeeds.
TEST_REFLECTION_TEMPLATE = (
    "Plan: {prompt_template}\n"
    "Intro: {feedback_intro}\n"
    "Body: {inputs_outputs_feedback}\n"
    "NRPV:\n{nrpv_block}\n"
    "Navigation Reproduction Patch Validation"
)


class MockLiteLLMModel:
    def __init__(
        self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"
    ):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking


class MockDefaultAgent:
    """Simulates a DefaultAgent that successfully submits."""

    last_kwargs: dict = {}
    last_env: Any = None

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        MockDefaultAgent.last_env = env
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": (
                    "## Improved Plan\n\n1. Analyze the bug\n"
                    "2. Fix the parser\n3. Add tests"
                ),
            },
        ]

    def run(self, task):
        return (
            "Submitted",
            "## Improved Plan\n\n1. Analyze the bug\n2. Fix the parser\n3. Add tests",
        )


class MockDefaultAgentFenced(MockDefaultAgent):
    """Agent output wrapped in ``` markdown fence (template requirement)."""

    def run(self, task):
        fenced = (
            "```markdown\n"
            "## Improved Plan\n\n1. Analyze the bug\n2. Fix the parser\n"
            "```"
        )
        return ("Submitted", fenced)


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "")


class MockDefaultAgentWhitespace(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "   \n\n   ")


class MockDefaultAgentShort(MockDefaultAgent):
    def run(self, task):
        return ("Submitted", "Too short.")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Step-limit exhaustion — falls back to last assistant message."""

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
            reflection_prompt_template=TEST_REFLECTION_TEMPLATE,
            reflect_instance_template="<pr_description>{{task}}</pr_description>",
            nrpv_block="## Navigation\n## Reproduction\n## Patch\n## Validation",
        ),
        agent=AgentConfig(max_steps=20),
        api_key="test-key",
    )


@pytest.fixture
def mock_env():
    return object()


class TestRunSuccess:
    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_returns_plan_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan, messages = reflect_agent.run(
            config,
            "previous plan content",
            "feedback intro text",
            "feedback body text",
            "Original issue text",
            mock_env,
        )

        assert len(plan) > 50
        assert "Improved Plan" in plan
        assert len(messages) == 3
        assert messages[0]["role"] == "system"

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_system_template_contains_all_placeholders(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config,
            "PREVIOUS-PLAN-MARKER",
            "INTRO-MARKER",
            "BODY-MARKER",
            "ISSUE-MARKER",
            mock_env,
        )

        assert "system_template" in MockDefaultAgent.last_kwargs
        st = MockDefaultAgent.last_kwargs["system_template"]
        # Plan, intro, body, and the nrpv block must all land in the system prompt
        assert "PREVIOUS-PLAN-MARKER" in st
        assert "INTRO-MARKER" in st
        assert "BODY-MARKER" in st
        # NRPV section names must appear (via the nrpv_block field)
        assert "Navigation" in st
        assert "Reproduction" in st
        assert "Patch" in st
        assert "Validation" in st

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_issue_description_passed_as_task(self, mock_import, config, mock_env):
        """The reflect agent must receive the original issue via ``task``
        so its reflect_instance_template can wrap it in <pr_description>."""

        captured: dict = {}

        class CapturingAgent(MockDefaultAgent):
            def run(self, task):
                captured["task"] = task
                return MockDefaultAgent.run(self, task)

        mock_import.return_value = (CapturingAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config,
            "previous plan",
            "intro",
            "body",
            "ISSUE-X",
            mock_env,
        )
        assert captured["task"] == "ISSUE-X"

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_reflect_instance_template_passed_to_agent(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config, "previous plan", "intro", "body", "issue", mock_env
        )
        assert "instance_template" in MockDefaultAgent.last_kwargs
        assert "{{task}}" in MockDefaultAgent.last_kwargs["instance_template"]

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_passes_docker_environment(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config, "previous plan", "intro", "body", "issue", mock_env
        )

        # environment is passed as positional arg to DefaultAgent, not in kwargs
        assert MockDefaultAgent.last_env is mock_env

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_extracts_fenced_plan(self, mock_import, config, mock_env):
        """LLM output inside ``` blocks is extracted by parse_output."""
        mock_import.return_value = (MockDefaultAgentFenced, MockLiteLLMModel, object)
        plan, _ = reflect_agent.run(
            config, "previous plan", "intro", "body", "issue", mock_env
        )

        # parse_output should strip the ``` fences
        assert "```" not in plan
        assert "## Improved Plan" in plan

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_limit_exceeded_raises_task_error(self, mock_import, config, mock_env):
        """When DefaultAgent hits a limit without submitting, raise TaskError."""
        mock_import.return_value = (
            MockDefaultAgentLimitExceeded,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(TaskError, match="terminated without a submission"):
            reflect_agent.run(
                config, "previous plan", "intro", "body", "issue", mock_env
            )


class TestRunValidation:
    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            reflect_agent.run(
                config, "previous plan", "intro", "body", "issue", mock_env
            )

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_whitespace_only_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (
            MockDefaultAgentWhitespace,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(TaskError, match="empty"):
            reflect_agent.run(
                config, "previous plan", "intro", "body", "issue", mock_env
            )

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_short_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentShort, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="too short"):
            reflect_agent.run(
                config, "previous plan", "intro", "body", "issue", mock_env
            )


class TestMissingDependency:
    @patch(
        "src.agents.reflect_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, mock_env):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            reflect_agent.run(
                config, "previous plan", "intro", "body", "issue", mock_env
            )
