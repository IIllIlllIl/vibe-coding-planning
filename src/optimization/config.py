"""Independent configuration for GEPA rule optimization."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config import DockerConfig
from src.optimization.hpc.config import HPCConfig


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
    agent_timeout_seconds: int = 0


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
    primary_metric: str = "accuracy"


@dataclass(frozen=True)
class OfflineSearchConfig(SearchConfig):
    """Offline-only search semantics layered over shared GEPA settings."""

    train_case_repetitions: int = 1


@dataclass(frozen=True)
class ContainerConfig:
    """Container runtime selection for GEPA (Docker or Apptainer)."""

    runtime: str = "docker"
    module: str = "tools/Apptainer"
    sif_cache_dir: Path = Path("/tmp/vibe-sif-cache")
    writable_tmpfs: bool = True


@dataclass(frozen=True)
class OfflineExecutionConfig:
    backend: str = "local"


@dataclass(frozen=True)
class TaskConfig:
    semantics: str = "historical_resolution"


@dataclass(frozen=True)
class BehavioralRepositoryConfig:
    backend: str = "temporal_git_proxy"
    repositories_root: Path = Path("/tmp/vibe-behavioral-mirrors")
    workspace_root: Path = Path("/tmp/vibe-behavioral-checkouts")


@dataclass(frozen=True)
class OptimizationConfig:
    dataset_snapshot: Path
    initial_rules_path: Path
    run_dir: Path
    checker: ModelConfig
    reflection: ModelConfig
    search: OfflineSearchConfig
    docker: DockerConfig
    container: ContainerConfig
    checker_prompt: str
    checker_instance_template: str
    reflection_prompt: str
    reflection_instance_template: str
    execution: OfflineExecutionConfig = field(
        default_factory=OfflineExecutionConfig
    )
    hpc: HPCConfig = field(default_factory=HPCConfig)
    task: TaskConfig = field(default_factory=TaskConfig)
    behavioral_repository: BehavioralRepositoryConfig = field(
        default_factory=BehavioralRepositoryConfig
    )


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
    agent_timeout_seconds = int(data.get("agent_timeout_seconds", 0))
    if agent_timeout_seconds < 0:
        raise ValueError("model agent_timeout_seconds must be non-negative")
    return ModelConfig(
        model=str(data["model"]),
        api_base=str(data["api_base"]),
        api_key_env=str(data.get("api_key_env", "DEEPSEEK_API_KEY")),
        temperature=temperature,
        max_steps=int(data.get("max_steps", 50)),
        cost_limit=float(data.get("cost_limit", 1.0)),
        timeout=int(data.get("timeout", 1800)),
        max_attempts=max_attempts,
        agent_timeout_seconds=agent_timeout_seconds,
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
    execution_data = _mapping(raw.get("execution", {}), "execution")
    task_data = _mapping(raw.get("task", {}), "task")
    repository_data = _mapping(
        raw.get("behavioral_repository", {}), "behavioral_repository"
    )
    hpc_data = _mapping(raw.get("hpc", {}), "hpc")
    prompts = _mapping(raw.get("prompts"), "prompts")
    paths = _mapping(raw.get("paths"), "paths")

    if require_api_keys:
        for model_config in (checker, reflection):
            if not os.environ.get(model_config.api_key_env):
                raise ValueError(
                    f"environment variable {model_config.api_key_env} is not set"
                )

    search = OfflineSearchConfig(
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
        primary_metric=str(search_data.get("primary_metric", "accuracy")),
        train_case_repetitions=int(
            search_data.get("train_case_repetitions", 1)
        ),
    )
    if min(
        search.max_metric_calls,
        search.projection_metric_calls,
        search.reflection_minibatch_size,
        search.parallel,
        search.train_case_repetitions,
    ) < 1:
        raise ValueError("search budgets and parallelism must be positive")
    if search.min_proposals < 0:
        raise ValueError("search.min_proposals must be non-negative")
    if search.max_iterations is not None and search.max_iterations < 1:
        raise ValueError("search.max_iterations must be positive when set")
    if search.primary_metric not in ("accuracy", "balanced_accuracy"):
        raise ValueError(
            "search.primary_metric must be 'accuracy' or 'balanced_accuracy'"
        )
    if search.primary_metric == "balanced_accuracy" and search.skip_perfect_score:
        raise ValueError(
            "search.skip_perfect_score must be false when primary_metric is "
            "balanced_accuracy because class-weighted examples do not share "
            "one perfect score"
        )

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
    if container.runtime not in ("docker", "apptainer", "none"):
        raise ValueError(
            "container.runtime must be 'docker', 'apptainer', or 'none', "
            f"got {container.runtime!r}"
        )
    execution = OfflineExecutionConfig(
        backend=str(execution_data.get("backend", "local")),
    )
    if execution.backend not in ("local", "hpc_slurm"):
        raise ValueError(
            "execution.backend must be 'local' or 'hpc_slurm'"
        )
    task = TaskConfig(
        semantics=str(task_data.get("semantics", "historical_resolution"))
    )
    if task.semantics not in (
        "historical_resolution",
        "behavioral_plan_acceptability_v1",
    ):
        raise ValueError(f"unsupported task.semantics: {task.semantics!r}")
    behavioral_repository = BehavioralRepositoryConfig(
        backend=str(repository_data.get("backend", "temporal_git_proxy")),
        repositories_root=resolve(
            str(
                repository_data.get("repositories_root", "/tmp/vibe-behavioral-mirrors")
            )
        ),
        workspace_root=resolve(
            str(repository_data.get("workspace_root", "/tmp/vibe-behavioral-checkouts"))
        ),
    )
    if behavioral_repository.backend != "temporal_git_proxy":
        raise ValueError("behavioral_repository.backend must be 'temporal_git_proxy'")
    hpc_defaults = HPCConfig()
    try:
        default_worker_config_path = str(config_path.relative_to(root))
    except ValueError:
        default_worker_config_path = str(config_path)
    retired_offline_hpc_fields = {
        "max_running_array_tasks",
        "array_concurrency",
    } & set(hpc_data)
    if retired_offline_hpc_fields:
        fields = ", ".join(sorted(retired_offline_hpc_fields))
        raise ValueError(
            "Offline HPC no longer accepts project-level Slurm concurrency "
            f"limits ({fields}); submit every Agent task and let Slurm schedule"
        )
    if execution.backend == "hpc_slurm" and search.parallel != 1:
        raise ValueError(
            "Offline HPC requires search.parallel=1 because Agent-task "
            "concurrency belongs to Slurm, not the GEPA controller"
        )
    if (
        execution.backend == "local"
        and checker.agent_timeout_seconds
        and search.parallel != 1
    ):
        raise ValueError(
            "Offline local execution with checker.agent_timeout_seconds "
            "requires search.parallel=1; the soft deadline interrupts one "
            "main-thread Agent session"
        )
    hpc = HPCConfig(
        submit=bool(hpc_data.get("submit", hpc_defaults.submit)),
        remote_project_dir=str(
            hpc_data.get("remote_project_dir", hpc_defaults.remote_project_dir)
        ),
        remote_task_dir=str(
            hpc_data.get("remote_task_dir", hpc_defaults.remote_task_dir)
        ),
        remote_env_file=str(
            hpc_data.get("remote_env_file", hpc_defaults.remote_env_file)
        ),
        ulhpc_config=str(
            hpc_data.get("ulhpc_config", hpc_defaults.ulhpc_config)
        ),
        partition=str(hpc_data.get("partition", hpc_defaults.partition)),
        cpus_per_task=int(
            hpc_data.get("cpus_per_task", hpc_defaults.cpus_per_task)
        ),
        mem=str(hpc_data.get("mem", hpc_defaults.mem)),
        time=str(hpc_data.get("time", hpc_defaults.time)),
        poll_interval_seconds=int(
            hpc_data.get(
                "poll_interval_seconds",
                hpc_defaults.poll_interval_seconds,
            )
        ),
        task_output_grace_seconds=int(
            hpc_data.get(
                "task_output_grace_seconds",
                hpc_defaults.task_output_grace_seconds,
            )
        ),
        missing_task_grace_seconds=int(
            hpc_data.get(
                "missing_task_grace_seconds",
                hpc_defaults.missing_task_grace_seconds,
            )
        ),
        max_task_attempts=int(
            hpc_data.get("max_task_attempts", hpc_defaults.max_task_attempts)
        ),
        python_module=str(
            hpc_data.get("python_module", hpc_defaults.python_module)
        ),
        container_module=str(
            hpc_data.get("container_module", hpc_defaults.container_module)
        ),
        python_bin=str(hpc_data.get("python_bin", hpc_defaults.python_bin)),
        job_name_prefix=str(
            hpc_data.get("job_name_prefix", hpc_defaults.job_name_prefix)
        ),
        worker_config_path=str(
            hpc_data.get("worker_config_path", default_worker_config_path)
        ),
    )
    if min(
        hpc.cpus_per_task,
        hpc.poll_interval_seconds,
        hpc.task_output_grace_seconds,
        hpc.missing_task_grace_seconds,
        hpc.max_task_attempts,
    ) < 1:
        raise ValueError("hpc numeric resources and retry settings must be positive")
    if task.semantics == "historical_resolution" and container.runtime == "none":
        raise ValueError("historical Offline execution requires a container runtime")
    if (
        execution.backend == "hpc_slurm"
        and task.semantics == "historical_resolution"
        and container.runtime != "apptainer"
    ):
        raise ValueError(
            "Offline HPC Slurm execution requires container.runtime: apptainer"
        )
    if (
        task.semantics == "behavioral_plan_acceptability_v1"
        and container.runtime != "none"
    ):
        raise ValueError("Behavioral v1 requires container.runtime: none")

    initial_guideline = paths.get("initial_guideline", paths.get("initial_rules"))
    if initial_guideline is None:
        raise ValueError("paths.initial_guideline is required")

    return OptimizationConfig(
        dataset_snapshot=resolve(str(paths["dataset_snapshot"])),
        # Internal attribute retained for compatibility with shared Online and
        # historical Offline plumbing; new Offline configs call this artifact
        # a guideline.
        initial_rules_path=resolve(str(initial_guideline)),
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
        execution=execution,
        hpc=hpc,
        task=task,
        behavioral_repository=behavioral_repository,
    )
