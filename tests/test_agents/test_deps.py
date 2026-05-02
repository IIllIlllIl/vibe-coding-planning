"""Tests for src/agents/_deps.py."""

from __future__ import annotations

import logging

import pytest

from src.agents import _deps
from src.agents._deps import build_default_agent
from src.exceptions import FatalError


class FakeAgentNoCostLimit:
    """Mimics older mini-swe-agent: no cost_limit kwarg."""

    def __init__(self, system_prompt, model, environment, max_steps=30):
        self.kwargs = {
            "system_prompt": system_prompt,
            "model": model,
            "environment": environment,
            "max_steps": max_steps,
        }


class FakeAgentWithCostLimit:
    """Mimics newer mini-swe-agent: has cost_limit kwarg."""

    def __init__(self, system_prompt, model, environment, max_steps=30, cost_limit=None):
        self.kwargs = {
            "system_prompt": system_prompt,
            "model": model,
            "environment": environment,
            "max_steps": max_steps,
            "cost_limit": cost_limit,
        }


@pytest.fixture(autouse=True)
def _reset_warn_flag():
    """Each test starts with the cost_limit warning un-emitted."""
    _deps._cost_limit_warned = False
    yield
    _deps._cost_limit_warned = False


class TestBuildDefaultAgentForwarding:
    def test_passes_cost_limit_when_supported(self):
        agent = build_default_agent(
            FakeAgentWithCostLimit,
            system_prompt="sp",
            model="m",
            environment="env",
            max_steps=10,
            cost_limit=1.5,
        )
        assert agent.kwargs["cost_limit"] == 1.5
        assert agent.kwargs["max_steps"] == 10

    def test_omits_cost_limit_when_not_passed(self):
        agent = build_default_agent(
            FakeAgentWithCostLimit,
            system_prompt="sp",
            model="m",
            environment="env",
            max_steps=10,
            cost_limit=None,
        )
        # When cost_limit=None, helper should not forward it (let dataclass default kick in)
        assert agent.kwargs["cost_limit"] is None  # defaulted by FakeAgent

    def test_drops_cost_limit_when_unsupported(self, caplog):
        with caplog.at_level(logging.WARNING):
            agent = build_default_agent(
                FakeAgentNoCostLimit,
                system_prompt="sp",
                model="m",
                environment="env",
                max_steps=10,
                cost_limit=2.0,
            )
        assert "cost_limit" not in agent.kwargs
        # warning emitted exactly once
        assert "does not accept 'cost_limit'" in caplog.text

    def test_drop_warning_only_once(self, caplog):
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                build_default_agent(
                    FakeAgentNoCostLimit,
                    system_prompt="sp",
                    model="m",
                    environment="env",
                    max_steps=10,
                    cost_limit=2.0,
                )
        # only one warning despite 3 calls
        assert caplog.text.count("does not accept 'cost_limit'") == 1


class TestImportMinisweagent:
    def test_raises_fatal_when_not_installed(self, monkeypatch):
        # Simulate import failure
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "minisweagent" or name.startswith("minisweagent."):
                raise ImportError("No module named 'minisweagent'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(FatalError, match="mini-swe-agent is not installed"):
            _deps.import_minisweagent()
