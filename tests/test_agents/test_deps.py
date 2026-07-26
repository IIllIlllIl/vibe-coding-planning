"""Tests for src/agents/_deps.py (mini-swe-agent 1.17.5 helpers)."""

from __future__ import annotations

import pytest

from src.agents import _deps
from src.agents._deps import (
    DEFAULT_FORMAT_ERROR_TEMPLATE,
    build_default_agent,
    build_model,
    extract_last_assistant,
    raise_for_permanent_provider_error,
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

    def test_auto_prefixes_anthropic(self):
        m = build_model(
            FakeLitellmModel,
            model_name="claude-sonnet-4",
            api_key="k",
            api_base="https://api.anthropic.com",
        )
        assert m.model_name == "anthropic/claude-sonnet-4"

    def test_auto_prefixes_kimi(self):
        m = build_model(
            FakeLitellmModel,
            model_name="kimi-for-coding",
            api_key="k",
            api_base="https://api.kimi.com/coding/",
        )
        assert m.model_name == "anthropic/kimi-for-coding"

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
        assert agent.kwargs["format_error_template"] == DEFAULT_FORMAT_ERROR_TEMPLATE
        assert agent.kwargs["step_limit"] == 15
        assert agent.kwargs["cost_limit"] == 1.5
        assert agent.model == "m"
        assert agent.env == "env"

    @pytest.mark.parametrize("action_count", [0, 2])
    def test_format_error_uses_official_swebench_correction(self, action_count):
        from jinja2 import StrictUndefined, Template

        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
        )
        rendered = Template(
            agent.kwargs["format_error_template"],
            undefined=StrictUndefined,
        ).render(actions=["action"] * action_count)

        assert f"found {action_count} actions" in rendered
        assert "EXACTLY ONE action in triple backticks" in rendered
        assert "```bash\n<action>\n```" in rendered

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

    def test_instance_template_preserved_verbatim(self):
        # build_default_agent must NOT touch the instance_template — the
        # ``{{task}}`` placeholder is rendered later by mini-swe-agent's
        # DefaultAgent.run(task=...) call, which inserts the task as a
        # Jinja variable value (single non-recursive pass). Pre-rendering
        # the placeholder here would inline the task into the template
        # source and crash on the second render pass when the task
        # contains Jinja-looking fragments.
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
        )
        assert agent.kwargs["instance_template"] == "<pr>{{task}}</pr>"

    def test_no_instance_template_means_no_kwarg(self):
        # When the caller does not provide an instance_template we let
        # mini-swe-agent fall back to its built-in default. We must NOT
        # synthesise one here.
        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template=None,
        )
        assert "instance_template" not in agent.kwargs


    # ------------------------------------------------------------------
    # Regression: the variable-injection path must survive mini-swe-agent's
    # actual Jinja2 + StrictUndefined render. This test exercises a real
    # ``jinja2.Template`` (not a mock) using the same render call
    # mini-swe-agent makes in ``DefaultAgent.run()``. The seven instances
    # that crashed in batch ``run4-full-500`` had problem_statements
    # containing patterns like ``{{test_run_form}}`` and ``{%s ...%}``;
    # the older pre-render path inlined those into the template source and
    # the second-pass renderer raised TemplateSyntaxError / UndefinedError.
    # With variable injection, the task content is a literal value and is
    # never re-parsed as template syntax.
    # ------------------------------------------------------------------
    def test_instance_template_renders_safely_with_hostile_task(self):
        from jinja2 import StrictUndefined, Template

        agent = build_default_agent(
            FakeDefaultAgent,
            model="m",
            environment="env",
            system_template="test",
            step_limit=10,
            instance_template="<pr>{{task}}</pr>",
        )
        rendered_template = agent.kwargs["instance_template"]
        hostile_task = (
            "Bug in test_run_form: {{test_run_form}} crashes on "
            "{%s ...%} and regex \\1 backref and C:\\Users\\dev"
        )
        # Simulate mini-swe-agent's render call.
        output = Template(rendered_template, undefined=StrictUndefined).render(
            task=hostile_task
        )
        # All hostile characters survive verbatim.
        assert "{{test_run_form}}" in output
        assert "{%s ...%}" in output
        assert r"\1 backref" in output
        assert r"C:\Users\dev" in output


class TestPermanentProviderErrors:
    @pytest.mark.parametrize(
        "name,message",
        [
            ("AuthenticationError", "Invalid API key"),
            ("APIError", "insufficient balance for this request"),
            ("RateLimitError", "insufficient_quota"),
        ],
    )
    def test_permanent_provider_failure_is_fatal(self, name, message):
        with pytest.raises(FatalError, match="Permanent model-provider failure"):
            raise_for_permanent_provider_error(name, message)

    def test_agent_cost_limit_is_not_provider_failure(self):
        raise_for_permanent_provider_error(
            "LimitsExceeded",
            "cost limit exceeded; insufficient balance wording from local report",
        )

    def test_transient_rate_limit_is_not_permanent(self):
        raise_for_permanent_provider_error(
            "RateLimitError",
            "Too many requests; retry after 30 seconds",
        )


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
