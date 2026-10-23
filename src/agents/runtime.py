"""Workflow-independent dispatch over the established Agent implementations.

Handlers are lazy: only the selected backend may create a model, inspect its
credentials or prepare its tools. Role adapters keep their existing submission
contracts and return values. This module owns no retry loop or experiment state.
"""
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar
import re

T = TypeVar("T")
CODEX_FIELDS = ("model", "reasoning_effort", "codex_binary", "codex_auth_file", "codex_version")
REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}


class AgentConfigurationError(ValueError):
    """A shared backend configuration cannot be repaired by rerunning an Agent."""
    retry_disposition = "block_run"


def executor_name(config: Mapping[str, Any]) -> str:
    name = config.get("executor", "mini_swe")
    if name not in ("mini_swe", "codex_cli"):
        raise AgentConfigurationError(f"unsupported Agent executor: {name}")
    return name


def codex_options(config: Mapping[str, Any]) -> dict[str, str]:
    """Resolve explicit pinned Native Codex options without loading credentials."""
    if set(config) & {"api_key", "token", "password"}:
        raise AgentConfigurationError("Agent credentials must remain in private remote state")
    if any(not isinstance(config.get(key), str) or not config[key].strip() for key in CODEX_FIELDS):
        raise AgentConfigurationError(f"Codex requires explicit {', '.join(CODEX_FIELDS)}")
    if config["reasoning_effort"] not in REASONING_EFFORTS:
        raise AgentConfigurationError("unsupported Codex reasoning_effort")
    return {"executor": "codex_cli", **{key: config[key] for key in CODEX_FIELDS}}


def credential_envs(configs: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Only mini-SWE uses provider-key environment variables in the job shell."""
    names = {str(config.get("api_key_env", "DEEPSEEK_API_KEY"))
             for config in configs if executor_name(config) == "mini_swe"}
    if any(name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names):
        raise AgentConfigurationError("provider credential must name an environment variable")
    return tuple(sorted(name for name in names if name))


def run_agent(config: Mapping[str, Any], *, mini_swe: Callable[[], T] | None = None,
              codex_cli: Callable[[], T] | None = None) -> T:
    """Invoke one backend once, preserving its exact result and exceptions.

    The role supplies adapters for its input/output contract, not another Agent
    loop. An unsupported backend fails explicitly; there is no fallback.
    """
    name = executor_name(config)
    handler = mini_swe if name == "mini_swe" else codex_cli
    if handler is None:
        raise AgentConfigurationError(f"Agent call has no {name} adapter")
    return handler()
