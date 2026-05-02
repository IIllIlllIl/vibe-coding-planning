"""Shared agent dependency imports.

Centralises lazy importing of mini-swe-agent and provides a
``build_default_agent`` helper that conditionally forwards
``cost_limit`` based on the installed mini-swe-agent version.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from src.exceptions import FatalError

logger = logging.getLogger(__name__)

_MINI_SWE_AGENT_VERSION = "~=1.0"
_cost_limit_warned = False


def import_minisweagent() -> Any:
    """Lazy import mini-swe-agent, raising FatalError if not installed."""
    try:
        from minisweagent import DefaultAgent, LiteLLMModel  # type: ignore[import-untyped]

        return DefaultAgent, LiteLLMModel  # type: ignore[return-value]
    except ImportError as exc:
        raise FatalError(
            f"mini-swe-agent is not installed. "
            f"Please install it: pip install mini-swe-agent{_MINI_SWE_AGENT_VERSION}"
        ) from exc


def build_default_agent(
    DefaultAgent: type,
    *,
    system_prompt: str,
    model: Any,
    environment: Any,
    max_steps: int,
    cost_limit: float | None = None,
) -> Any:
    """Construct a ``DefaultAgent`` with optional ``cost_limit`` support.

    The mini-swe-agent constructor signature varies between versions; not
    all versions accept a ``cost_limit`` kwarg. This helper inspects the
    constructor once: if ``cost_limit`` is supported, the value is
    forwarded (true hard limit); otherwise the value is dropped with a
    one-time warning, and the caller is responsible for setting a budget
    alert in the DeepSeek dashboard as the real backstop.

    Note that we accept ``DefaultAgent`` as an explicit argument (rather
    than importing it inside this function) so that existing tests which
    patch ``src.agents.<role>_agent.import_minisweagent`` continue to
    work without further refactoring.

    Args:
        DefaultAgent: The DefaultAgent class (typically returned by
            ``import_minisweagent()`` in the calling module).
        system_prompt: System prompt for the agent.
        model: A model instance (e.g. ``LiteLLMModel``).
        environment: Tool execution environment.
        max_steps: Maximum number of agent steps.
        cost_limit: Optional soft cost limit. Forwarded to the agent only
            if its constructor accepts the kwarg.

    Returns:
        A ``DefaultAgent`` instance.
    """
    global _cost_limit_warned

    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "model": model,
        "environment": environment,
        "max_steps": max_steps,
    }

    if cost_limit is not None:
        param_names: set[str] = set()
        try:
            param_names = set(inspect.signature(DefaultAgent).parameters.keys())
        except (TypeError, ValueError):
            pass

        if "cost_limit" in param_names:
            kwargs["cost_limit"] = cost_limit
        elif not _cost_limit_warned:
            logger.warning(
                "DefaultAgent does not accept 'cost_limit' parameter; "
                "value (%s) is ignored. The effective per-agent cost cap is "
                "max_steps × per-step token price. Set a DeepSeek dashboard "
                "budget alert as the real backstop.",
                cost_limit,
            )
            _cost_limit_warned = True

    return DefaultAgent(**kwargs)
