"""Tests for src/agents/reflect_agent.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.agents import reflect_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


# Reflection template stub used by the test suite.  We do NOT depend on
# the YAML-resident template here because the agent's behaviour is
# independent of template wording — the assertions below just need the
# four Jinja placeholders to be present so the variable-injection
# contract can be exercised.
TEST_REFLECTION_TEMPLATE = (
    "Plan: {{prompt_template}}\n"
    "Intro: {{feedback_intro}}\n"
    "Body: {{inputs_outputs_feedback}}\n"
    "NRPV:\n{{nrpv_block}}\n"
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
    last_run_kwargs: dict = {}
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

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return (
            "Submitted",
            "## Improved Plan\n\n1. Analyze the bug\n2. Fix the parser\n3. Add tests",
        )


class MockDefaultAgentFenced(MockDefaultAgent):
    """Agent output wrapped in ``` markdown fence (template requirement)."""

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        fenced = (
            "```markdown\n"
            "## Improved Plan\n\n1. Analyze the bug\n2. Fix the parser\n"
            "```"
        )
        return ("Submitted", fenced)


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "")


class MockDefaultAgentWhitespace(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "   \n\n   ")


class MockDefaultAgentShort(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "Too short.")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Step-limit exhaustion — falls back to last assistant message."""

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
    def test_system_template_passed_verbatim(
        self, mock_import, config, mock_env
    ):
        """The reflection_prompt_template must be forwarded to
        DefaultAgent unchanged — no host-side str.format inlining of
        the four placeholders. Inlining LLM-generated content
        (prompt_template, inputs_outputs_feedback) into the template
        source would crash mini-swe-agent's second-pass StrictUndefined
        Jinja render on any ``{{...}}`` / ``{%...%}`` fragments.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config,
            "PREVIOUS-PLAN-MARKER",
            "INTRO-MARKER",
            "BODY-MARKER",
            "ISSUE-MARKER",
            mock_env,
        )

        st = MockDefaultAgent.last_kwargs["system_template"]
        # All four placeholders preserved verbatim.
        assert "{{prompt_template}}" in st
        assert "{{feedback_intro}}" in st
        assert "{{inputs_outputs_feedback}}" in st
        assert "{{nrpv_block}}" in st
        # None of the runtime content is inlined into the template source.
        assert "PREVIOUS-PLAN-MARKER" not in st
        assert "INTRO-MARKER" not in st
        assert "BODY-MARKER" not in st

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_all_four_vars_injected_via_agent_run(
        self, mock_import, config, mock_env
    ):
        """The four runtime values plus ``task`` must flow to mini-swe-agent
        as ``run(**kwargs)``; the agent merges them into
        ``extra_template_vars`` before the (single-pass, non-recursive)
        Jinja render."""
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config,
            "PREVIOUS-PLAN-MARKER",
            "INTRO-MARKER",
            "BODY-MARKER",
            "ISSUE-MARKER",
            mock_env,
        )

        rk = MockDefaultAgent.last_run_kwargs
        assert rk["task"] == "ISSUE-MARKER"
        assert rk["prompt_template"] == "PREVIOUS-PLAN-MARKER"
        assert rk["feedback_intro"] == "INTRO-MARKER"
        assert rk["inputs_outputs_feedback"] == "BODY-MARKER"
        assert rk["nrpv_block"] == config.prompts.nrpv_block

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_reflect_instance_template_passed_to_agent(
        self, mock_import, config, mock_env
    ):
        """The configured instance_template is forwarded verbatim.

        ``{{task}}`` stays as a Jinja placeholder; the issue text is
        injected by mini-swe-agent's ``agent.run(task=...)`` via
        ``extra_template_vars`` (single-pass variable substitution).
        Pre-rendering here would inline the issue into the template
        source and crash on the second Jinja pass for issues containing
        template-like fragments.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        reflect_agent.run(
            config, "previous plan", "intro", "body", "issue", mock_env
        )
        assert "instance_template" in MockDefaultAgent.last_kwargs
        it = MockDefaultAgent.last_kwargs["instance_template"]
        # Placeholder preserved; issue text NOT inlined.
        assert "{{task}}" in it
        assert "issue" not in it

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
