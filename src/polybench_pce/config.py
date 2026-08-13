"""Configuration isolated from Online and Offline GEPA modes."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

from src.config import DockerConfig
from src.optimization.config import ContainerConfig, ModelConfig
from src.optimization.hpc.config import HPCConfig


@dataclass(frozen=True)
class PCEExecutionConfig:
    code_phase_timeout_seconds: int = 2400


@dataclass(frozen=True)
class PolyBenchPCEConfig:
    config_path: Path
    dataset_snapshot: Path
    image_manifest: Path
    run_dir: Path
    plan: ModelConfig
    code: ModelConfig
    docker: DockerConfig
    container: ContainerConfig
    execution: PCEExecutionConfig
    hpc: HPCConfig
    plan_prompt: str
    plan_instance_template: str
    code_prompt: str
    code_instance_template: str
    nrpv_block: str
    evaluator_timeout: int


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _model(value: dict[str, Any], *, temperature: float) -> ModelConfig:
    return ModelConfig(
        model=str(value["model"]),
        api_base=str(value["api_base"]),
        api_key_env=str(value.get("api_key_env", "DEEPSEEK_API_KEY")),
        temperature=float(value.get("temperature", temperature)),
        max_steps=int(value.get("max_steps", 0)),
        cost_limit=float(value.get("cost_limit", 0.0)),
        timeout=int(value.get("timeout", 1800)),
        max_attempts=1,
    )


def load_polybench_pce_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> PolyBenchPCEConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "polybench_pce":
        raise ValueError("PolyBench PCE config requires mode: polybench_pce")
    root = (
        config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    )

    def resolve(value: str) -> Path:
        expanded = os.path.expandvars(value)
        candidate = Path(expanded).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    paths = _mapping(raw.get("paths"), "paths")
    plan = _model(_mapping(raw.get("plan"), "plan"), temperature=0.0)
    code = _model(_mapping(raw.get("code"), "code"), temperature=0.0)
    for model in (plan, code):
        if require_api_keys and not os.environ.get(model.api_key_env):
            raise ValueError(f"environment variable {model.api_key_env} is not set")
    prompts = _mapping(raw.get("prompts"), "prompts")
    container_raw = _mapping(raw.get("container"), "container")
    execution_raw = _mapping(raw.get("execution", {}), "execution")
    hpc_raw = _mapping(raw.get("hpc"), "hpc")
    evaluator_raw = _mapping(raw.get("evaluator"), "evaluator")
    docker_raw = _mapping(raw.get("docker", {}), "docker")

    container = ContainerConfig(
        runtime=str(container_raw.get("runtime", "apptainer")),
        module=str(container_raw.get("module", "tools/Apptainer")),
        sif_cache_dir=resolve(str(container_raw["sif_cache_dir"])),
        writable_tmpfs=bool(container_raw.get("writable_tmpfs", True)),
    )
    if container.runtime != "apptainer":
        raise ValueError("PolyBench PCE supports only container.runtime: apptainer")
    hpc_defaults = HPCConfig()
    if "max_running_array_tasks" in hpc_raw or "array_concurrency" in hpc_raw:
        raise ValueError(
            "PolyBench PCE submits every instance task and leaves concurrency to Slurm"
        )
    hpc = HPCConfig(
        submit=bool(hpc_raw.get("submit", False)),
        remote_project_dir=str(
            hpc_raw.get("remote_project_dir", hpc_defaults.remote_project_dir)
        ),
        remote_task_dir=str(
            hpc_raw.get("remote_task_dir", hpc_defaults.remote_task_dir)
        ),
        remote_env_file=str(
            hpc_raw.get("remote_env_file", hpc_defaults.remote_env_file)
        ),
        ulhpc_config=str(hpc_raw.get("ulhpc_config", hpc_defaults.ulhpc_config)),
        partition=str(hpc_raw.get("partition", hpc_defaults.partition)),
        cpus_per_task=int(hpc_raw.get("cpus_per_task", 1)),
        mem=str(hpc_raw.get("mem", "4G")),
        time=str(hpc_raw.get("time", "02:05:00")),
        poll_interval_seconds=int(hpc_raw.get("poll_interval_seconds", 300)),
        task_output_grace_seconds=int(hpc_raw.get("task_output_grace_seconds", 300)),
        missing_task_grace_seconds=int(hpc_raw.get("missing_task_grace_seconds", 600)),
        max_task_attempts=int(hpc_raw.get("max_task_attempts", 3)),
        python_module=str(hpc_raw.get("python_module", hpc_defaults.python_module)),
        container_module=str(
            hpc_raw.get("container_module", hpc_defaults.container_module)
        ),
        python_bin=str(hpc_raw.get("python_bin", hpc_defaults.python_bin)),
        job_name_prefix=str(hpc_raw.get("job_name_prefix", "polybench-pce")),
        worker_config_path=str(hpc_raw.get("worker_config_path", str(config_path))),
    )
    if hpc.max_task_attempts != 3:
        raise ValueError(
            "PolyBench PCE currently requires exactly three total attempts"
        )
    if hpc.cpus_per_task != 1 or hpc.mem != "4G":
        raise ValueError("PolyBench PCE worker resources must remain 1 CPU / 4G")
    execution = PCEExecutionConfig(
        code_phase_timeout_seconds=int(
            execution_raw.get("code_phase_timeout_seconds", 2400)
        )
    )
    if execution.code_phase_timeout_seconds < 0:
        raise ValueError("code_phase_timeout_seconds must be non-negative")
    evaluator_timeout = int(evaluator_raw.get("timeout", 1800))
    if evaluator_timeout < 1:
        raise ValueError("evaluator.timeout must be positive")

    return PolyBenchPCEConfig(
        config_path=config_path,
        dataset_snapshot=resolve(str(paths["dataset_snapshot"])),
        image_manifest=resolve(str(paths["image_manifest"])),
        run_dir=resolve(str(paths["run_dir"])),
        plan=plan,
        code=code,
        docker=DockerConfig(
            workdir=str(docker_raw.get("workdir", "/testbed")),
            timeout=int(docker_raw.get("timeout", 1800)),
            delete_images_after_instance=False,
            min_free_gb=int(docker_raw.get("min_free_gb", 20)),
            max_cached_images=int(docker_raw.get("max_cached_images", 75)),
            polybench_build_fallback=False,
        ),
        container=container,
        execution=execution,
        hpc=hpc,
        plan_prompt=str(prompts["plan_system"]),
        plan_instance_template=str(prompts["plan_instance"]),
        code_prompt=str(prompts["code_system"]),
        code_instance_template=str(prompts["code_instance"]),
        nrpv_block=str(prompts.get("nrpv_block", "")),
        evaluator_timeout=evaluator_timeout,
    )
