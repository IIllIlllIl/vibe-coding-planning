"""Configuration loading, validation, and defaults.

Loads config.yaml and validates parameters. Reads the API key from the
``DEEPSEEK_API_KEY`` environment variable (the env var name is kept for
backward compatibility) and stores it in the provider-agnostic
``Config.api_key`` field.
"""

from __future__ import annotations

import logging
import os
import re
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
    """System-level runtime configuration.

    The ``dataset`` field selects which SWE-bench dataset to load from
    Hugging Face. ``instances`` are interpreted within that dataset:
    a single run only ever touches one dataset (Phase 1 = Verified,
    Phase 2 = PolyBench Python). The default is the Verified dataset.

    ``batch_id`` is a mandatory folder-name segment that isolates one
    experimental run from another in the output tree:
    ``output/<dataset>/<batch_id>/<instance_id>/``. It must be passed
    via ``config.yaml`` (``system.batch_id``) or the CLI flag
    ``--batch-id``; the YAML loader validates it via
    :func:`validate_batch_id`. The dataclass default ``"default"`` is a
    safety net for in-process construction (test fixtures); production
    flows always go through ``load_config``, which rejects empty or
    malformed values.
    """

    n: int = 3
    optimization_info_level: int = 1
    model: str = "deepseek-v4-flash"
    api_base: str = "https://api.deepseek.com"
    dataset: str = "SWE-bench/SWE-bench_Verified"
    dataset_type: str = ""
    language_filter: str = ""
    instances: list[str] = field(default_factory=list)
    output_dir: str = "./output"
    batch_id: str = "default"
    skip_completed_rounds: bool = True
    resume: ResumeConfig = field(default_factory=ResumeConfig)


@dataclass(frozen=True)
class PromptConfig:
    """Prompt template configuration.

    All prompt strings are sourced from the YAML config file. The
    ``nrpv_block`` field is the single source of truth for the
    Navigation/Reproduction/Patch/Validation plan structure and is
    substituted into both ``plan_generation_prompt`` and
    ``reflection_prompt_template`` via the ``{nrpv_block}`` placeholder.
    """

    plan_generation_prompt: str = ""
    plan_instance_template: str = ""
    code_generation_prompt: str = ""
    code_instance_template: str = ""
    reflection_prompt_template: str = ""
    reflect_instance_template: str = ""
    check_prompt: str = ""
    check_instance_template: str = ""
    nrpv_block: str = ""


@dataclass(frozen=True)
class DockerConfig:
    """Docker environment configuration."""

    image_builder_script: str = "./scripts/build_docker_images.sh"
    workdir: str = "/testbed"
    timeout: int = 30
    delete_images_after_instance: bool = True
    min_free_gb: int = 20
    max_cached_images: int = 75
    polybench_build_fallback: bool = True
    polybench_pull_timeout: int = 600


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
    timeout: int = 1800


@dataclass(frozen=True)
class EvaluatorConfig:
    """SWE-bench evaluator configuration."""

    timeout: int = 1800


@dataclass(frozen=True)
class CheckerConfig:
    """Configuration for the plan checker agent.

    The checker validates generated plans against a rule set before
    code execution. It can use a different (typically cheaper) model
    than the main pipeline.
    """

    enabled: bool = False
    rules_path: str = "./output/analysis_pro/aggregated_rules.json"
    model: str = "deepseek-v4-flash"
    api_base: str = "https://api.deepseek.com"
    max_steps: int = 50
    cost_limit: float = 1.0


@dataclass(frozen=True)
class AnalysisConfig:
    """Configuration for the contrastive rule extraction analysis.

    Supports independent model settings from the main pipeline so that
    analysis can run against a different provider (e.g. Moonshot/kimi)
    while the pipeline uses DeepSeek.
    """

    model: str = "moonshot/kimi-k2.6"
    api_base: str = "https://api.moonshot.cn"
    api_key_env: str = "MOONSHOT_API_KEY"
    backend: str = "mini_swe"
    max_steps: int = 1000
    cost_limit: float = 50.0
    output_dir: str = "./output/analysis_results"
    parallel: int = 1
    enable_review: bool = False
    opencode_bin: str = "opencode"
    opencode_xdg_data_home: str = ""
    opencode_isolate_per_case: bool = True
    opencode_timeout: int = 900
    rate_limit_sleep_seconds: int = 18000
    max_retries: int = 2
    # Model family for provider-aware prompt tuning.
    # Valid values: "auto", "deepseek", "kimi", "openai", "anthropic".
    # When "auto", the family is inferred from api_base.
    model_family: str = "auto"
    # Optional suffix appended to the contrastive agent's system prompt.
    # Leave empty to use the default suffix for the detected model_family.
    system_prompt_suffix: str = ""


@dataclass(frozen=True)
class Config:
    """Top-level configuration object."""

    system: SystemConfig = field(default_factory=SystemConfig)
    prompts: PromptConfig = field(default_factory=PromptConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    evaluator: EvaluatorConfig = field(default_factory=EvaluatorConfig)
    checker: CheckerConfig = field(default_factory=CheckerConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    api_key: str = ""
    analysis_api_key: str = ""


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


def _validate_non_negative_int(name: str, value: int) -> int:
    """Validate that an integer parameter is >= 0."""
    if value < 0:
        raise FatalError(f"Parameter '{name}' must be >= 0, got {value}")
    return value


# Whitelist for batch_id characters. Constrained to filesystem-safe ASCII so
# the value can be used directly as a directory name on any platform without
# escaping. Allows letters, digits, underscore, hyphen, dot. Explicitly
# excludes path separators ("/", "\"), "..", and whitespace to block path
# traversal and accidental nesting.
_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")


def validate_batch_id(value: str) -> str:
    """Return ``value`` if it is a valid batch_id, else raise FatalError.

    A batch_id is the folder-name segment between dataset and instance in
    the output tree. It is mandatory and must be filesystem-safe:

    * non-empty after stripping whitespace
    * contains only ``[A-Za-z0-9_.-]``
    * is not ``"."`` or ``".."`` (path-traversal guard)

    Exposed at module scope (not underscored) so that CLI override paths
    can re-validate after merging command-line flags into the loaded
    config.
    """
    stripped = (value or "").strip()
    if not stripped:
        raise FatalError(
            "system.batch_id is required and must be non-empty. "
            "Set it in config.yaml (e.g. 'run3_level1_n3') or pass "
            "--batch-id on the command line."
        )
    if stripped in (".", ".."):
        raise FatalError(
            f"system.batch_id={stripped!r} is reserved (path traversal). "
            "Choose a descriptive name like 'run3_level1_n3'."
        )
    if not _BATCH_ID_PATTERN.match(stripped):
        raise FatalError(
            f"system.batch_id={stripped!r} contains illegal characters. "
            "Only letters, digits, '_', '-', and '.' are allowed."
        )
    return stripped


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
        dataset=_get_str(data, "dataset", "SWE-bench/SWE-bench_Verified"),
        dataset_type=_get_str(data, "dataset_type", ""),
        language_filter=_get_str(data, "language_filter", ""),
        instances=_get_list(data, "instances"),
        output_dir=_get_str(data, "output_dir", "./output"),
        batch_id=validate_batch_id(_get_str(data, "batch_id", "")),
        skip_completed_rounds=_get_bool(data, "skip_completed_rounds", True),
        resume=_build_resume_config(resume_data),
    )


def _build_prompt_config(data: dict[str, Any]) -> PromptConfig:
    """Build PromptConfig from a dict.

    All prompt strings come from ``config.yaml``; there is no Python-side
    default for the reflection template. Missing or empty fields produce
    an empty string here, and the agent will fail loudly when it tries to
    render the prompt — keeping ``config.yaml`` as the single source of
    truth for prompt design.
    """
    return PromptConfig(
        plan_generation_prompt=_get_str(data, "plan_generation_prompt", ""),
        plan_instance_template=_get_str(data, "plan_instance_template", ""),
        code_generation_prompt=_get_str(data, "code_generation_prompt", ""),
        code_instance_template=_get_str(data, "code_instance_template", ""),
        reflection_prompt_template=_get_str(data, "reflection_prompt_template", ""),
        reflect_instance_template=_get_str(data, "reflect_instance_template", ""),
        check_prompt=_get_str(data, "check_prompt", ""),
        check_instance_template=_get_str(data, "check_instance_template", ""),
        nrpv_block=_get_str(data, "nrpv_block", ""),
    )


def _build_docker_config(data: dict[str, Any]) -> DockerConfig:
    """Build DockerConfig from a dict."""
    return DockerConfig(
        image_builder_script=_get_str(data, "image_builder_script", "./scripts/build_docker_images.sh"),
        workdir=_get_str(data, "workdir", "/testbed"),
        timeout=_validate_positive_int("docker.timeout", _get_int(data, "timeout", 30)),
        delete_images_after_instance=_get_bool(data, "delete_images_after_instance", True),
        min_free_gb=_validate_positive_int("docker.min_free_gb", _get_int(data, "min_free_gb", 20)),
        max_cached_images=_validate_positive_int(
            "docker.max_cached_images", _get_int(data, "max_cached_images", 75)
        ),
        polybench_build_fallback=_get_bool(data, "polybench_build_fallback", True),
        polybench_pull_timeout=_validate_positive_int(
            "docker.polybench_pull_timeout",
            _get_int(data, "polybench_pull_timeout", 600),
        ),
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


def _build_checker_config(data: dict[str, Any]) -> CheckerConfig:
    """Build CheckerConfig from a dict."""
    import urllib.parse

    api_base = _get_str(data, "api_base", "https://api.deepseek.com")
    parsed = urllib.parse.urlparse(api_base)
    if not parsed.scheme or not parsed.netloc:
        raise FatalError(
            f"Invalid checker.api_base URL: {api_base}. Must include scheme and host."
        )

    return CheckerConfig(
        enabled=_get_bool(data, "enabled", False),
        rules_path=_get_str(data, "rules_path", "./output/analysis_pro/aggregated_rules.json"),
        model=_get_str(data, "model", "deepseek-v4-flash"),
        api_base=api_base,
        max_steps=_validate_positive_int("checker.max_steps", _get_int(data, "max_steps", 50)),
        cost_limit=_validate_non_negative_float("checker.cost_limit", _get_float(data, "cost_limit", 1.0)),
    )


def _build_analysis_config(data: dict[str, Any]) -> AnalysisConfig:
    """Build AnalysisConfig from a dict."""
    import urllib.parse

    backend = _get_str(data, "backend", "mini_swe")
    if backend not in {"mini_swe", "opencode"}:
        raise FatalError(
            f"Invalid analysis.backend: {backend}. Expected 'mini_swe' or 'opencode'."
        )

    api_base = _get_str(data, "api_base", "https://api.moonshot.cn")
    parsed = urllib.parse.urlparse(api_base)
    if not parsed.scheme or not parsed.netloc:
        raise FatalError(
            f"Invalid analysis.api_base URL: {api_base}. Must include scheme and host."
        )

    return AnalysisConfig(
        model=_get_str(data, "model", "moonshot/kimi-k2.6"),
        api_base=api_base,
        api_key_env=_get_str(data, "api_key_env", "MOONSHOT_API_KEY"),
        backend=backend,
        max_steps=_validate_positive_int(
            "analysis.max_steps", _get_int(data, "max_steps", 1000)
        ),
        cost_limit=_validate_non_negative_float(
            "analysis.cost_limit", _get_float(data, "cost_limit", 50.0)
        ),
        output_dir=_get_str(data, "output_dir", "./output/analysis_results"),
        parallel=_validate_positive_int("analysis.parallel", _get_int(data, "parallel", 1)),
        enable_review=_get_bool(data, "enable_review", False),
        opencode_bin=_get_str(data, "opencode_bin", "opencode"),
        opencode_xdg_data_home=_get_str(data, "opencode_xdg_data_home", ""),
        opencode_isolate_per_case=_get_bool(data, "opencode_isolate_per_case", True),
        opencode_timeout=_validate_positive_int(
            "analysis.opencode_timeout", _get_int(data, "opencode_timeout", 900)
        ),
        rate_limit_sleep_seconds=_validate_positive_int(
            "analysis.rate_limit_sleep_seconds",
            _get_int(data, "rate_limit_sleep_seconds", 18000),
        ),
        max_retries=_validate_non_negative_int(
            "analysis.max_retries", _get_int(data, "max_retries", 2)
        ),
        model_family=_get_str(data, "model_family", "auto"),
        system_prompt_suffix=_get_str(data, "system_prompt_suffix", ""),
    )


def load_config(path: str | Path) -> Config:
    """Load configuration from a YAML file.

    Reads the API key from the ``DEEPSEEK_API_KEY`` environment variable
    (kept as the canonical env var for backward compatibility) and stores
    it in ``Config.api_key``. Raises FatalError if the key is not set or
    if the config file cannot be parsed.
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

    checker_data = raw.get("checker", {})
    if not isinstance(checker_data, dict):
        checker_data = {}

    analysis_data = raw.get("analysis", {})
    if not isinstance(analysis_data, dict):
        analysis_data = {}

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise FatalError(
            "Environment variable DEEPSEEK_API_KEY is not set. "
            "Please set it before running the system."
        )

    analysis_config = _build_analysis_config(analysis_data)
    analysis_api_key = os.environ.get(analysis_config.api_key_env, "")
    if not analysis_api_key and analysis_config.backend != "opencode":
        logger.warning(
            "Environment variable %s is not set. Analysis agent will fail if used.",
            analysis_config.api_key_env,
        )

    return Config(
        system=_build_system_config(system_data),
        prompts=_build_prompt_config(prompts_data),
        docker=_build_docker_config(docker_data),
        agent=_build_agent_config(agent_data),
        evaluator=_build_evaluator_config(evaluator_data),
        checker=_build_checker_config(checker_data),
        analysis=analysis_config,
        api_key=api_key,
        analysis_api_key=analysis_api_key,
    )
