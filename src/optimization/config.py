"""Independent configuration for GEPA rule optimization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.config import DockerConfig


@dataclass(frozen=True)
class ModelConfig:
    model: str
    api_base: str
    api_key_env: str
    temperature: float
    max_steps: int
    cost_limit: float
    timeout: int
    max_attempts: int = 1


@dataclass(frozen=True)
class SearchConfig:
    max_metric_calls: int
    projection_metric_calls: int
    reflection_minibatch_size: int
    seed: int
    parallel: int
    skip_perfect_score: bool = True
    min_proposals: int = 0
    max_iterations: int | None = None


@dataclass(frozen=True)
class ContainerConfig:
    """Container runtime selection for GEPA (Docker or Apptainer)."""

    runtime: str = "docker"
    module: str = "tools/Apptainer"
    sif_cache_dir: Path = Path("/tmp/vibe-sif-cache")
    writable_tmpfs: bool = True


@dataclass(frozen=True)
class OptimizationConfig:
    dataset_snapshot: Path
    initial_rules_path: Path
    run_dir: Path
    checker: ModelConfig
    reflection: ModelConfig
    search: SearchConfig
    docker: DockerConfig
    container: ContainerConfig
    checker_prompt: str
    checker_instance_template: str
    reflection_prompt: str
    reflection_instance_template: str


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _model(data: dict[str, Any], *, checker: bool) -> ModelConfig:
    temperature = float(data.get("temperature", 0.0 if checker else 0.7))
    if checker and temperature != 0.0:
        raise ValueError("checker.temperature must be exactly 0.0")
    max_attempts = int(data.get("max_attempts", 1))
    if max_attempts < 1:
        raise ValueError("model max_attempts must be positive")
    return ModelConfig(
        model=str(data["model"]),
        api_base=str(data["api_base"]),
        api_key_env=str(data.get("api_key_env", "DEEPSEEK_API_KEY")),
        temperature=temperature,
        max_steps=int(data.get("max_steps", 50)),
        cost_limit=float(data.get("cost_limit", 1.0)),
        timeout=int(data.get("timeout", 1800)),
        max_attempts=max_attempts,
    )


def load_optimization_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> OptimizationConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    checker = _model(_mapping(raw.get("checker"), "checker"), checker=True)
    reflection = _model(
        _mapping(raw.get("reflection"), "reflection"),
        checker=False,
    )
    search_data = _mapping(raw.get("search"), "search")
    docker_data = _mapping(raw.get("docker", {}), "docker")
    container_data = _mapping(raw.get("container", {}), "container")
    prompts = _mapping(raw.get("prompts"), "prompts")
    paths = _mapping(raw.get("paths"), "paths")

    if require_api_keys:
        for model_config in (checker, reflection):
            if not os.environ.get(model_config.api_key_env):
                raise ValueError(
                    f"environment variable {model_config.api_key_env} is not set"
                )

    search = SearchConfig(
        max_metric_calls=int(search_data["max_metric_calls"]),
        projection_metric_calls=int(
            search_data.get(
                "projection_metric_calls",
                search_data["max_metric_calls"],
            )
        ),
        reflection_minibatch_size=int(
            search_data.get("reflection_minibatch_size", 3)
        ),
        seed=int(search_data.get("seed", 42)),
        parallel=int(search_data.get("parallel", 1)),
        skip_perfect_score=bool(search_data.get("skip_perfect_score", True)),
        min_proposals=int(search_data.get("min_proposals", 0)),
        max_iterations=(
            int(search_data["max_iterations"])
            if search_data.get("max_iterations") is not None
            else None
        ),
    )
    if min(
        search.max_metric_calls,
        search.projection_metric_calls,
        search.reflection_minibatch_size,
        search.parallel,
    ) < 1:
        raise ValueError("search budgets and parallelism must be positive")
    if search.min_proposals < 0:
        raise ValueError("search.min_proposals must be non-negative")
    if search.max_iterations is not None and search.max_iterations < 1:
        raise ValueError("search.max_iterations must be positive when set")

    docker = DockerConfig(
        workdir=str(docker_data.get("workdir", "/testbed")),
        timeout=int(docker_data.get("timeout", 30)),
        delete_images_after_instance=bool(
            docker_data.get("delete_images_after_instance", True)
        ),
        min_free_gb=int(docker_data.get("min_free_gb", 20)),
        max_cached_images=int(docker_data.get("max_cached_images", 75)),
        polybench_build_fallback=False,
    )

    def resolve(raw_path: str) -> Path:
        raw_path = os.path.expandvars(raw_path)
        candidate = Path(raw_path)
        return candidate if candidate.is_absolute() else root / candidate

    container = ContainerConfig(
        runtime=str(container_data.get("runtime", "docker")),
        module=str(container_data.get("module", "tools/Apptainer")),
        sif_cache_dir=resolve(str(container_data.get("sif_cache_dir", "/tmp/vibe-sif-cache"))),
        writable_tmpfs=bool(container_data.get("writable_tmpfs", True)),
    )
    if container.runtime not in ("docker", "apptainer"):
        raise ValueError(
            f"container.runtime must be 'docker' or 'apptainer', got {container.runtime!r}"
        )

    return OptimizationConfig(
        dataset_snapshot=resolve(str(paths["dataset_snapshot"])),
        initial_rules_path=resolve(str(paths["initial_rules"])),
        run_dir=resolve(str(paths["run_dir"])),
        checker=checker,
        reflection=reflection,
        search=search,
        docker=docker,
        container=container,
        checker_prompt=str(prompts["checker_system"]),
        checker_instance_template=str(prompts["checker_instance"]),
        reflection_prompt=str(prompts["reflection_system"]),
        reflection_instance_template=str(prompts["reflection_instance"]),
    )
