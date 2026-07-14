"""Configuration for online GEPA planning optimization."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.config import DockerConfig
from src.optimization.config import ContainerConfig, ModelConfig, SearchConfig


@dataclass(frozen=True)
class OnlineDatasetConfig:
    dataset: str = "SWE-bench/SWE-bench_Verified"
    dataset_type: str = ""
    language_filter: str = ""
    train_instance_ids: tuple[str, ...] = ()
    validation_instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OnlineExecutionConfig:
    backend: str = "local_docker"
    controller_yield_after_submit: bool = False


@dataclass(frozen=True)
class OnlineEvaluatorConfig:
    timeout: int = 1800
    backend: str = "swebench_docker"


@dataclass(frozen=True)
class OnlineHPCConfig:
    """ULHPC job-array settings for online rollout workers.

    These defaults intentionally request the smallest fairshare-friendly
    resources we currently expect to need. Resource increases should be based
    on sacct measurements from pilot workers. ``max_running_array_tasks`` is
    Slurm array scheduling concurrency, not parallelism inside one worker.
    """

    submit: bool = False
    remote_project_dir: str = "~/hpc_runs/vibe-coding-planning-online"
    remote_task_dir: str = ""
    remote_env_file: str = "~/.config/vibe-coding-planning/deepseek.env"
    ulhpc_config: str = "configs/ulhpc_submit.yaml"
    partition: str = "batch"
    cpus_per_task: int = 1
    mem: str = "4G"
    time: str = "02:00:00"
    max_running_array_tasks: int = 5
    poll_interval_seconds: int = 300
    task_output_grace_seconds: int = 300
    missing_task_grace_seconds: int = 600
    max_task_attempts: int = 2
    python_module: str = "lang/Python/3.11"
    container_module: str = "tools/Apptainer"
    python_bin: str = "python3"
    job_name_prefix: str = "online-gepa-rollout"
    worker_config_path: str = ""


@dataclass(frozen=True)
class OnlineOptimizationConfig:
    dataset_snapshot: Path
    initial_rules_path: Path
    run_dir: Path
    dataset: OnlineDatasetConfig
    plan: ModelConfig
    code: ModelConfig
    reflection: ModelConfig
    search: SearchConfig
    docker: DockerConfig
    container: ContainerConfig
    execution: OnlineExecutionConfig
    hpc: OnlineHPCConfig
    plan_prompt: str
    plan_instance_template: str
    code_prompt: str
    code_instance_template: str
    reflection_prompt: str
    reflection_instance_template: str
    nrpv_block: str
    evaluator: OnlineEvaluatorConfig

    @property
    def evaluator_timeout(self) -> int:
        return self.evaluator.timeout


def _default_hpc_root() -> str:
    user = os.environ.get("ULHPC_USER") or os.environ.get("USER") or "<user>"
    return os.environ.get(
        "VIBE_HPC_ROOT",
        f"/scratch/users/{user}/vibe-coding-planning",
    )


def _default_remote_task_dir() -> str:
    return f"{_default_hpc_root()}/online-rollout-tasks"


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _model(data: dict[str, Any], *, default_temperature: float) -> ModelConfig:
    max_attempts = int(data.get("max_attempts", 1))
    if max_attempts < 1:
        raise ValueError("model max_attempts must be positive")
    return ModelConfig(
        model=str(data["model"]),
        api_base=str(data["api_base"]),
        api_key_env=str(data.get("api_key_env", "DEEPSEEK_API_KEY")),
        temperature=float(data.get("temperature", default_temperature)),
        max_steps=int(data.get("max_steps", 50)),
        cost_limit=float(data.get("cost_limit", 1.0)),
        timeout=int(data.get("timeout", 1800)),
        max_attempts=max_attempts,
    )


def load_online_optimization_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> OnlineOptimizationConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "online_planning":
        raise ValueError("online GEPA config must set mode: online_planning")
    root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()

    paths = _mapping(raw.get("paths"), "paths")
    dataset_data = _mapping(raw.get("dataset", {}), "dataset")
    plan = _model(_mapping(raw.get("plan"), "plan"), default_temperature=0.0)
    code = _model(_mapping(raw.get("code"), "code"), default_temperature=0.0)
    reflection = _model(
        _mapping(raw.get("reflection"), "reflection"),
        default_temperature=0.7,
    )
    search_data = _mapping(raw.get("search"), "search")
    docker_data = _mapping(raw.get("docker", {}), "docker")
    container_data = _mapping(raw.get("container", {}), "container")
    execution_data = _mapping(raw.get("execution", {}), "execution")
    hpc_data = _mapping(raw.get("hpc", {}), "hpc")
    prompts = _mapping(raw.get("prompts"), "prompts")
    evaluator_data = _mapping(raw.get("evaluator", {}), "evaluator")

    if require_api_keys:
        for model_config in (plan, code, reflection):
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

    def resolve(raw_path: str) -> Path:
        raw_path = os.path.expandvars(raw_path)
        candidate = Path(raw_path)
        return candidate if candidate.is_absolute() else root / candidate

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
    execution = OnlineExecutionConfig(
        backend=str(execution_data.get("backend", "local_docker")),
        controller_yield_after_submit=bool(
            execution_data.get("controller_yield_after_submit", False)
        ),
    )
    if execution.backend not in ("local_docker", "hpc_slurm"):
        raise ValueError(
            "execution.backend must be 'local_docker' or 'hpc_slurm', "
            f"got {execution.backend!r}"
        )
    evaluator = OnlineEvaluatorConfig(
        timeout=int(evaluator_data.get("timeout", 1800)),
        backend=str(
            evaluator_data.get(
                "backend",
                "swebench_apptainer"
                if container.runtime == "apptainer"
                else "swebench_docker",
            )
        ),
    )
    if evaluator.backend not in ("swebench_docker", "swebench_apptainer"):
        raise ValueError(
            "evaluator.backend must be 'swebench_docker' or "
            f"'swebench_apptainer', got {evaluator.backend!r}"
        )
    if execution.backend == "hpc_slurm" and evaluator.backend == "swebench_docker":
        raise ValueError(
            "online HPC Slurm execution must not use evaluator.backend "
            "'swebench_docker'; use 'swebench_apptainer'"
        )
    if container.runtime == "docker" and evaluator.backend == "swebench_apptainer":
        raise ValueError(
            "evaluator.backend 'swebench_apptainer' requires "
            "container.runtime: apptainer"
        )
    if container.runtime == "apptainer" and evaluator.backend == "swebench_docker":
        raise ValueError(
            "container.runtime: apptainer must not use Docker evaluator backend"
        )
    hpc_defaults = OnlineHPCConfig()
    try:
        default_worker_config_path = str(config_path.relative_to(root))
    except ValueError:
        default_worker_config_path = str(config_path)
    hpc = OnlineHPCConfig(
        submit=bool(hpc_data.get("submit", False)),
        remote_project_dir=str(
            hpc_data.get(
                "remote_project_dir",
                hpc_defaults.remote_project_dir,
            )
        ),
        remote_task_dir=str(
            hpc_data.get(
                "remote_task_dir",
                hpc_defaults.remote_task_dir or _default_remote_task_dir(),
            )
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
        max_running_array_tasks=int(
            hpc_data.get(
                "max_running_array_tasks",
                hpc_data.get(
                    "array_concurrency",
                    hpc_defaults.max_running_array_tasks,
                ),
            )
        ),
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
        hpc.max_running_array_tasks,
        hpc.poll_interval_seconds,
        hpc.task_output_grace_seconds,
        hpc.missing_task_grace_seconds,
        hpc.max_task_attempts,
    ) < 1:
        raise ValueError("hpc numeric resources and retry settings must be positive")

    return OnlineOptimizationConfig(
        dataset_snapshot=resolve(str(paths["dataset_snapshot"])),
        initial_rules_path=resolve(str(paths["initial_rules"])),
        run_dir=resolve(str(paths["run_dir"])),
        dataset=OnlineDatasetConfig(
            dataset=str(dataset_data.get("name", "SWE-bench/SWE-bench_Verified")),
            dataset_type=str(dataset_data.get("type", "")),
            language_filter=str(dataset_data.get("language_filter", "")),
            train_instance_ids=tuple(
                str(item) for item in dataset_data.get("train_instance_ids", [])
            ),
            validation_instance_ids=tuple(
                str(item)
                for item in dataset_data.get("validation_instance_ids", [])
            ),
        ),
        plan=plan,
        code=code,
        reflection=reflection,
        search=search,
        docker=docker,
        container=container,
        execution=execution,
        hpc=hpc,
        plan_prompt=str(prompts["plan_system"]),
        plan_instance_template=str(prompts["plan_instance"]),
        code_prompt=str(prompts["code_system"]),
        code_instance_template=str(prompts["code_instance"]),
        reflection_prompt=str(prompts["reflection_system"]),
        reflection_instance_template=str(prompts["reflection_instance"]),
        nrpv_block=str(prompts["nrpv_block"]),
        evaluator=evaluator,
    )
