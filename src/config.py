"""Configuration loading, validation, and defaults.

Loads config.yaml and validates parameters. Reads DEEPSEEK_API_KEY from
environment variables.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.exceptions import FatalError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumeConfig:
    """Resume configuration for rerunning from an existing plan."""

    enabled: bool = False
    from_plan_id: str = ""
    from_round: int = 0
    trajectories_dir: str = ""
    patches_dir: str = ""


@dataclass(frozen=True)
class SystemConfig:
    """System-level runtime configuration."""

    n: int = 3
    optimization_info_level: int = 1
    model: str = "deepseek-v4-flash"
    api_base: str = "https://api.deepseek.com"
    swe_pro_instances: list[str] = field(default_factory=list)
    output_dir: str = "./output"
    use_gepa_reflection_prompt: bool = True
    resume: ResumeConfig = field(default_factory=ResumeConfig)


@dataclass(frozen=True)
class PromptConfig:
    """Prompt template configuration."""

    plan_generation_prompt: str = ""
    code_generation_prompt: str = ""
    code_instance_template: str = ""
    plan_optimization_prompt: str = ""
    plan_format_template: str = ""


@dataclass(frozen=True)
class DockerConfig:
    """Docker environment configuration."""

    image_builder_script: str = "./scripts/build_docker_images.sh"
    workdir: str = "/testbed"
    codebase_mount_options: str = "ro"
    timeout: int = 30


@dataclass(frozen=True)
class AgentConfig:
    """Agent behavior configuration.

    Note on ``cost_limit``: This is a *soft* limit. It is forwarded to
    ``DefaultAgent`` only if the installed mini-swe-agent version exposes
    a matching constructor parameter (probed at runtime by
    ``src.agents._deps.build_default_agent``). When the parameter is not
    supported, the value is ignored with a one-time warning, and the
    effective per-agent spend ceiling is governed by
    ``max_steps × per-step token cost × DeepSeek price``. Always set a
    budget alert in the DeepSeek dashboard as the real backstop.
    """

    max_steps: int = 30
    cost_limit: float = 3.0
    timeout: int = 120


@dataclass(frozen=True)
class EvaluatorConfig:
    """SWE-bench evaluator configuration."""

    timeout: int = 1800


@dataclass(frozen=True)
class Config:
    """Top-level configuration object."""

    system: SystemConfig = field(default_factory=SystemConfig)
    prompts: PromptConfig = field(default_factory=PromptConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    deepseek_api_key: str = ""


def _load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    path = Path(path)
    if not path.exists():
        raise FatalError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_str(data: dict[str, Any], key: str, default: str = "") -> str:
    """Safely get a string value from a dict."""
    value = data.get(key, default)
    return str(value) if value is not None else default


def _get_int(data: dict[str, Any], key: str, default: int) -> int:
    """Safely get an int value from a dict."""
    value = data.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_float(data: dict[str, Any], key: str, default: float) -> float:
    """Safely get a float value from a dict."""
    value = data.get(key, default)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    """Safely get a bool value from a dict."""
    value = data.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


def _get_list(data: dict[str, Any], key: str, default: list[str] | None = None) -> list[str]:
    """Safely get a list of strings from a dict."""
    default = default or []
    value = data.get(key, default)
    if value is None:
        return default
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return default


def _validate_n(n: int) -> int:
    """Validate that n >= 1."""
    if n < 1:
        raise FatalError(f"Parameter 'n' must be >= 1, got {n}")
    return n


def _validate_optimization_info_level(level: int) -> int:
    """Validate optimization_info_level; default to 0 if invalid."""
    if level not in (0, 1):
        logger.warning(
            "optimization_info_level must be 0 or 1, got %s. Defaulting to 0.",
            level,
        )
        return 0
    return level


def _validate_positive_int(name: str, value: int) -> int:
    """Validate that an integer parameter is >= 1."""
    if value < 1:
        raise FatalError(f"Parameter '{name}' must be >= 1, got {value}")
    return value


def _validate_non_negative_float(name: str, value: float) -> float:
    """Validate that a float parameter is >= 0."""
    if value < 0:
        logger.warning("Parameter '%s' must be >= 0, got %s. Defaulting to 0.", name, value)
        return 0.0
    return value


def _build_resume_config(data: dict[str, Any]) -> ResumeConfig:
    """Build ResumeConfig from a dict, validating required fields when enabled."""
    enabled = _get_bool(data, "enabled", False)
    from_plan_id = _get_str(data, "from_plan_id", "")
    from_round = _get_int(data, "from_round", 0)

    if enabled:
        if not from_plan_id:
            logger.warning("resume.enabled is true but from_plan_id is empty")
        if from_round < 1:
            logger.warning("resume.enabled is true but from_round < 1 (%s)", from_round)

    return ResumeConfig(
        enabled=enabled,
        from_plan_id=from_plan_id,
        from_round=from_round,
        trajectories_dir=_get_str(data, "trajectories_dir", ""),
        patches_dir=_get_str(data, "patches_dir", ""),
    )


def _build_system_config(data: dict[str, Any]) -> SystemConfig:
    """Build SystemConfig from a dict."""
    import urllib.parse

    n = _validate_n(_get_int(data, "n", 3))
    opt_level = _validate_optimization_info_level(_get_int(data, "optimization_info_level", 1))
    use_gepa = _get_bool(data, "use_gepa_reflection_prompt", True)

    api_base = _get_str(data, "api_base", "https://api.deepseek.com")
    parsed = urllib.parse.urlparse(api_base)
    if not parsed.scheme or not parsed.netloc:
        raise FatalError(
            f"Invalid api_base URL: {api_base}. Must include scheme and host."
        )

    resume_data = data.get("resume", {})
    if not isinstance(resume_data, dict):
        resume_data = {}

    return SystemConfig(
        n=n,
        optimization_info_level=opt_level,
        model=_get_str(data, "model", "deepseek-v4-flash"),
        api_base=api_base,
        swe_pro_instances=_get_list(data, "swe_pro_instances"),
        output_dir=_get_str(data, "output_dir", "./output"),
        use_gepa_reflection_prompt=use_gepa,
        resume=_build_resume_config(resume_data),
    )


def _build_prompt_config(data: dict[str, Any]) -> PromptConfig:
    """Build PromptConfig from a dict."""
    return PromptConfig(
        plan_generation_prompt=_get_str(data, "plan_generation_prompt", ""),
        code_generation_prompt=_get_str(data, "code_generation_prompt", ""),
        code_instance_template=_get_str(data, "code_instance_template", ""),
        plan_optimization_prompt=_get_str(data, "plan_optimization_prompt", ""),
        plan_format_template=_get_str(data, "plan_format_template", ""),
    )


def _build_docker_config(data: dict[str, Any]) -> DockerConfig:
    """Build DockerConfig from a dict."""
    return DockerConfig(
        image_builder_script=_get_str(data, "image_builder_script", "./scripts/build_docker_images.sh"),
        workdir=_get_str(data, "workdir", "/testbed"),
        codebase_mount_options=_get_str(data, "codebase_mount_options", "ro"),
        timeout=_validate_positive_int("docker.timeout", _get_int(data, "timeout", 30)),
    )


def _build_agent_config(data: dict[str, Any]) -> AgentConfig:
    """Build AgentConfig from a dict."""
    return AgentConfig(
        max_steps=_validate_positive_int("agent.max_steps", _get_int(data, "max_steps", 30)),
        cost_limit=_validate_non_negative_float("agent.cost_limit", _get_float(data, "cost_limit", 3.0)),
        timeout=_validate_positive_int("agent.timeout", _get_int(data, "timeout", 120)),
    )


def _build_evaluator_config(data: dict[str, Any]) -> EvaluatorConfig:
    """Build EvaluatorConfig from a dict."""
    return EvaluatorConfig(
        timeout=_validate_positive_int("evaluator.timeout", _get_int(data, "timeout", 1800)),
    )


def load_config(path: str | Path) -> Config:
    """Load configuration from a YAML file.

    Reads DEEPSEEK_API_KEY from the environment. Raises FatalError if the key
    is not set or if the config file cannot be parsed.
    """
    raw = _load_yaml(path)

    system_data = raw.get("system", {})
    if not isinstance(system_data, dict):
        system_data = {}

    prompts_data = raw.get("prompts", {})
    if not isinstance(prompts_data, dict):
        prompts_data = {}

    docker_data = raw.get("docker", {})
    if not isinstance(docker_data, dict):
        docker_data = {}

    agent_data = raw.get("agent", {})
    if not isinstance(agent_data, dict):
        agent_data = {}

    evaluator_data = raw.get("evaluator", {})
    if not isinstance(evaluator_data, dict):
        evaluator_data = {}

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise FatalError(
            "Environment variable DEEPSEEK_API_KEY is not set. "
            "Please set it before running the system."
        )

    return Config(
        system=_build_system_config(system_data),
        prompts=_build_prompt_config(prompts_data),
        docker=_build_docker_config(docker_data),
        agent=_build_agent_config(agent_data),
        evaluator=_build_evaluator_config(evaluator_data),
        deepseek_api_key=api_key,
    )
