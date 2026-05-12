"""Tests for src/agents/_deps.py (mini-swe-agent 1.17.5 helpers)."""

from __future__ import annotations

import pytest

from src.agents import _deps
from src.agents._deps import (
    build_default_agent,
    build_model,
    extract_last_assistant,
)
from src.exceptions import FatalError


class FakeLitellmModel:
    """Mimics LitellmModel constructor signature."""

    def __init__(self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking


class FakeDefaultAgent:
    """Mimics DefaultAgent constructor signature (model, env, **config_kwargs)."""

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        self.kwargs = kwargs
        self.messages: list[dict] = []


class TestBuildModel:
    def test_passes_model_name_and_kwargs(self):
        m = build_model(
            FakeLitellmModel,
            model_name="deepseek/deepseek-chat",
            api_key="sk-test",
            api_base="https://api.deepseek.com",
        )
        assert m.model_name == "deepseek/deepseek-chat"
        assert m.model_kwargs["api_key"] == "sk-test"
        assert m.model_kwargs["api_base"] == "https://api.deepseek.com"

    def test_auto_prefixes_deepseek(self):
        m = build_model(
            FakeLitellmModel,
            model_name="deepseek-v4-flash",
            api_key="k",
            api_base="https://api.deepseek.com",
        )
        assert m.model_name == "deepseek/deepseek-v4-flash"

    def test_auto_prefixes_openai(self):
        m = build_model(
            FakeLitellmModel,
            model_name="gpt-4",
            api_key="k",
            api_base="https://api.openai.com",
        )
        assert m.model_name == "openai/gpt-4"

    def test_unchanged_when_prefix_present(self):
        m = build_model(
            FakeLitellmModel,
            model_name="custom/provider-model",
            api_key="k",
            api_base="https://example.com",
        )
        assert m.model_name == "custom/provider-model"

    def test_unknown_domain_warns_and_returns_unchanged(self, caplog):
        with caplog.at_level("WARNING"):
            m = build_model(
                FakeLitellmModel,
                model_name="some-model",
                api_key="k",
                api_base="https://unknown.example.com",
            )
        assert m.model_name == "some-model"
        assert "Could not infer litellm provider prefix" in caplog.text


class TestBuildDefaultAgent:
    def test_forwards_all_kwargs(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="You are a planner",
            step_limit=15,
            cost_limit=1.5,
        )
        assert agent.kwargs["system_template"] == "You are a planner"
        assert agent.kwargs["step_limit"] == 15
        assert agent.kwargs["cost_limit"] == 1.5
        assert agent.model == "m"
        assert agent.env == "env"

    def test_omits_cost_limit_when_none(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            cost_limit=None,
        )
        assert "cost_limit" not in agent.kwargs

    def test_task_pre_renders_instance_template(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
            task="hello {world}",
        )
        it = agent.kwargs["instance_template"]
        assert "{{task}}" not in it
        assert "<pr>hello {world}</pr>" == it

    def test_task_pre_renders_default_template_when_none(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template=None,
            task="issue with {braces}",
        )
        it = agent.kwargs["instance_template"]
        assert "{{task}}" not in it
        assert "issue with {braces}" in it

    def test_task_none_leaves_template_untouched(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="keep {{task}} here",
            task=None,
        )
        assert agent.kwargs["instance_template"] == "keep {{task}} here"

    # ------------------------------------------------------------------
    # Regression: re.sub replacement-string backreference interpretation.
    # If the substitution is done with `re.sub(pat, task, ...)`, any
    # ``\1``-``\9`` or ``\g<name>`` inside ``task`` is treated as a group
    # reference and raises ``re.error: invalid group reference``. SWE-bench
    # problem_statements occasionally contain such sequences (regex
    # examples, escape demos, Windows paths in stack traces) and would
    # otherwise crash the agent setup before any LLM call. The fix passes
    # the replacement via a lambda so the string is inserted verbatim.
    # ------------------------------------------------------------------
    def test_task_with_backslash_digit_is_inserted_literally(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
            task=r"see regex \1 backref",
        )
        # The literal backslash-digit must survive into the rendered template.
        assert agent.kwargs["instance_template"] == r"<pr>see regex \1 backref</pr>"

    def test_task_with_backslash_g_named_group_is_inserted_literally(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
            task=r"use \g<name> to refer",
        )
        assert agent.kwargs["instance_template"] == r"<pr>use \g<name> to refer</pr>"

    def test_task_with_windows_path_survives_substitution(self):
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
            task=r"file C:\Users\dev\repo\test.py line 42",
        )
        assert (
            agent.kwargs["instance_template"]
            == r"<pr>file C:\Users\dev\repo\test.py line 42</pr>"
        )

    def test_task_with_backslash_works_in_default_template(self):
        # The instance_template=None branch concatenates `task` with string
        # literals and never calls re.sub, but verify the no-template path
        # also tolerates regex-like content for parity.
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template=None,
            task=r"task with \1 and \g<x>",
        )
        it = agent.kwargs["instance_template"]
        assert r"task with \1 and \g<x>" in it


class TestExtractLastAssistant:
    def test_extracts_last_assistant(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "plan A"},
            {"role": "assistant", "content": "plan B"},
        ]
        assert extract_last_assistant(messages) == "plan B"

    def test_returns_empty_when_no_assistant(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ]
        assert extract_last_assistant(messages) == ""

    def test_returns_empty_for_empty_list(self):
        assert extract_last_assistant([]) == ""


class TestImportMinisweagent:
    def test_raises_fatal_when_not_installed(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "minisweagent" or name.startswith("minisweagent."):
                raise ImportError("No module named 'minisweagent'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(FatalError, match="mini-swe-agent is not installed"):
            _deps.import_minisweagent()
