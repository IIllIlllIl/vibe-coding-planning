"""Shared agent dependency imports (mini-swe-agent 1.17.5).

Provides lazy imports, model/agent construction helpers, and output
extraction utilities adapted to the mini-swe-agent 1.17.5 API surface.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from src.exceptions import FatalError

logger = logging.getLogger(__name__)
_MINI_SWE_AGENT_VERSION = "1.17.5"


def import_minisweagent() -> Any:
    """Lazy import mini-swe-agent 1.17.5 classes.

    Returns a 3-tuple: (DefaultAgent, LitellmModel, DockerEnvironment).
    """
    try:
        from minisweagent.agents.default import DefaultAgent  # type: ignore[import-untyped]
        from minisweagent.models.litellm_model import LitellmModel  # type: ignore[import-untyped]
        from minisweagent.environments.docker import DockerEnvironment  # type: ignore[import-untyped]

        return DefaultAgent, LitellmModel, DockerEnvironment  # type: ignore[return-value]
    except ImportError as exc:
        raise FatalError(
            f"mini-swe-agent is not installed. "
            f"Please install it: pip install mini-swe-agent{_MINI_SWE_AGENT_VERSION}"
        ) from exc


def _infer_litellm_prefix(model_name: str, api_base: str) -> str:
    """Infer litellm provider prefix from api_base when user didn't provide one.

    Litellm requires provider-prefixed model names (e.g. ``openai/gpt-4``).
    If ``model_name`` already contains a ``/``, it is returned unchanged.
    Otherwise the prefix is guessed from the api_base domain so that users
    can keep config clean (``deepseek-v4-flash`` instead of
    ``deepseek/deepseek-v4-flash``).

    Args:
        model_name: Raw model name from config (may already contain ``/``).
        api_base: API base URL from config.

    Returns:
        Litellm-compatible model name.
    """
    if "/" in model_name:
        return model_name

    domain = urllib.parse.urlparse(api_base).netloc.lower()

    if "deepseek" in domain:
        return f"deepseek/{model_name}"
    if "openai" in domain:
        return f"openai/{model_name}"
    if "anthropic" in domain:
        return f"anthropic/{model_name}"
    if "kimi.com" in domain:
        return f"anthropic/{model_name}"

    logger.warning(
        "Could not infer litellm provider prefix for api_base=%s. "
        "If the call fails with 'Provider NOT provided', set the full "
        "litellm model name in config (e.g. 'provider/model-name').",
        api_base,
    )
    return model_name


def build_model(
    LitellmModel: type,
    model_name: str,
    api_key: str,
    api_base: str,
) -> Any:
    """Build a LitellmModel with provider-prefixed model name.

    ``LitellmModel`` is passed as an explicit argument (rather than
    imported inside this function) so that tests which patch
    ``import_minisweagent`` can inject a mock without also patching
    ``_deps`` internals.

    Args:
        LitellmModel: The LitellmModel class.
        model_name: Model identifier. If it does not contain a ``/`` the
            prefix is inferred from ``api_base``.
        api_key: API key for the LLM provider.
        api_base: Provider base URL.

    Returns:
        A ``LitellmModel`` instance.
    """
    prefixed = _infer_litellm_prefix(model_name, api_base)
    logger.info("Building LitellmModel: resolved_name=%s", prefixed)

    return LitellmModel(
        model_name=prefixed,
        model_kwargs={"api_key": api_key, "api_base": api_base},
        cost_tracking="ignore_errors",
    )


def build_default_agent(
    DefaultAgent: type,
    model: Any,
    environment: Any,
    *,
    system_template: str,
    step_limit: int,
    cost_limit: float | None = None,
    instance_template: str | None = None,
) -> Any:
    """Build a DefaultAgent with explicit config kwargs.

    mini-swe-agent 1.17.5 signature::

        DefaultAgent(model, env, *, config_class=AgentConfig, **kwargs)

    We pass only the config fields (``system_template``, ``instance_template``,
    ``step_limit``, ``cost_limit``) as keyword arguments and let DefaultAgent
    forward them to its internal ``AgentConfig``. ``DefaultAgent`` is passed
    as an explicit argument so that tests can inject a mock class without
    patching ``_deps`` internals.

    Task injection follows the official ``minisweagent/config/extra/swebench.yaml``
    pattern: the ``instance_template`` contains a literal ``{{task}}``
    placeholder, and the task string is supplied at call time via
    ``agent.run(task=<issue_description>)``. mini-swe-agent's
    ``DefaultAgent.run`` puts ``task`` into ``extra_template_vars`` and
    renders the template with Jinja2's ``StrictUndefined`` — variable
    substitution is a single, non-recursive pass, so any Jinja-looking
    fragments inside the task content (``{{var}}``, ``{%s ...%}``, regex
    backrefs, Windows paths) are inserted verbatim and never re-parsed as
    template syntax. Earlier revisions of this function tried to pre-render
    ``{{task}}`` here, which inlined the task into the template *source*
    and triggered ``TemplateSyntaxError`` / ``UndefinedError`` on the
    second render pass — that crash took out 7 of 450 instances in batch
    ``run4-full-500`` before being diagnosed.

    Args:
        DefaultAgent: The DefaultAgent class.
        model: A model instance (e.g. ``LitellmModel``).
        environment: Tool execution environment.
        system_template: System prompt text.
        step_limit: Maximum number of agent steps.
        cost_limit: Optional cost limit.
        instance_template: Optional first-user-message template. Should
            contain a literal ``{{task}}`` placeholder. When omitted,
            mini-swe-agent falls back to its built-in default template.

    Returns:
        A ``DefaultAgent`` instance. Call ``agent.run(task=<issue>)`` to
        inject the task.
    """
    kwargs: dict[str, Any] = {
        "system_template": system_template,
        "step_limit": step_limit,
    }
    if cost_limit is not None:
        kwargs["cost_limit"] = cost_limit

    if instance_template is not None:
        kwargs["instance_template"] = instance_template

    return DefaultAgent(model, environment, **kwargs)


def extract_last_assistant(messages: list[dict[str, Any]]) -> str:
    """Extract the content of the last assistant message.

    mini-swe-agent 1.17.5's ``DefaultAgent.run()`` returns
    ``(exception_name, exception_message)`` rather than the generated text.
    The actual output is stored in the conversation history as the last
    message whose ``role`` is ``"assistant"``.

    Args:
        messages: The agent's message history.

    Returns:
        The assistant message content, or an empty string if none found.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""
