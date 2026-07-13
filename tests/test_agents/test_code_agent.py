"""Tests for src/agents/code_agent.py."""

from __future__ import annotations

import json
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
    last_run_kwargs: dict = {}

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "diff output"},
        ]

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return (
            "Submitted",
            "diff --git a/file.py b/file.py\n"
            "--- a/file.py\n+++ b/file.py\n"
            "@@ -1 +1 @@\n-fix\n+fixed\n",
        )


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "")


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Simulates step-limit exhaustion — must raise TaskError, no fallback."""

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
            code_generation_prompt=(
                "You are a coder.\n\nPlan: {{plan}}\n\nIssue: {{task}}"
            ),
            code_instance_template="<pr_description>{{task}}</pr_description>",
        ),
        agent=AgentConfig(max_steps=20),
        api_key="test-key",
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
    def test_system_template_passed_verbatim(self, mock_import, config, mock_env):
        """The code_generation_prompt must be forwarded to DefaultAgent
        unchanged — no host-side str.replace inlining of ``{{plan}}``.
        Inlining would re-parse LLM-generated plan content as Jinja2
        template syntax on the second render pass (e.g. Django/Sympy
        bug plans frequently contain ``{{var}}`` and ``{% tag %}``
        fragments which would crash StrictUndefined).
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(
            config, "PLAN-CONTENT-MARKER", "ISSUE-CONTENT-MARKER", mock_env
        )

        st = MockDefaultAgent.last_kwargs["system_template"]
        # Placeholder preserved verbatim.
        assert "{{plan}}" in st
        # Plan content NOT inlined into the template source.
        assert "PLAN-CONTENT-MARKER" not in st
        # Same goes for the task placeholder.
        assert "{{task}}" in st
        assert "ISSUE-CONTENT-MARKER" not in st

    @patch("src.agents.code_agent.import_minisweagent")
    def test_plan_and_task_injected_via_agent_run(
        self, mock_import, config, mock_env
    ):
        """``plan`` and ``task`` must flow to mini-swe-agent as
        ``run(**kwargs)`` values; mini-swe-agent merges them into
        ``extra_template_vars`` before the (single-pass, non-recursive)
        Jinja render, so content stays a literal variable VALUE.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(config, "Plan to fix bug", "Parser fails", mock_env)

        assert MockDefaultAgent.last_run_kwargs["task"] == "Parser fails"
        assert MockDefaultAgent.last_run_kwargs["plan"] == "Plan to fix bug"

    @patch("src.agents.code_agent.import_minisweagent")
    def test_instance_template_passed_to_agent(self, mock_import, config, mock_env):
        """The official SWE-bench instance_template must be forwarded verbatim.

        ``{{task}}`` is preserved as a Jinja placeholder — mini-swe-agent's
        ``DefaultAgent.run(task=...)`` injects the issue description at
        render time via ``extra_template_vars``.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(config, "Plan", "Issue", mock_env)
        assert "instance_template" in MockDefaultAgent.last_kwargs
        it = MockDefaultAgent.last_kwargs["instance_template"]
        assert "{{task}}" in it
        assert "Issue" not in it

    @patch("src.agents.code_agent.import_minisweagent")
    def test_blank_instance_template_falls_back_to_default(self, mock_import, mock_env):
        """When config provides no instance_template, we omit the kwarg
        entirely and let mini-swe-agent use its built-in default
        template (which itself contains ``{{task}}`` and is rendered
        safely via the variable-injection path).
        """
        cfg = Config(
            system=SystemConfig(model="deepseek-v4-flash", api_base="https://api.deepseek.com"),
            prompts=PromptConfig(code_generation_prompt="x"),  # no code_instance_template
            agent=AgentConfig(max_steps=20),
            api_key="test-key",
        )
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        code_agent.run(cfg, "Plan", "Issue", mock_env)
        # No instance_template kwarg means DefaultAgent will use its
        # built-in default (see minisweagent.agents.default.AgentConfig).
        assert "instance_template" not in MockDefaultAgent.last_kwargs


class TestRunValidation:
    @patch("src.agents.code_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            code_agent.run(config, "Plan", "Issue", mock_env)

    @patch("src.agents.code_agent.import_minisweagent")
    def test_empty_output_persists_failure_trajectory(
        self, mock_import, config, mock_env, tmp_path
    ):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        path = tmp_path / "failed_code_trajectory.json"
        with pytest.raises(TaskError, match="empty"):
            code_agent.run(
                config,
                "Plan",
                "Issue",
                mock_env,
                failure_trajectory_path=path,
            )

        record = json.loads(path.read_text())
        assert record["exit_status"] == "Submitted"
        assert record["exit_message"] == ""
        assert record["messages"][-1]["role"] == "assistant"

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
