"""Configuration for the independent SWE-Verified PCE workflow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any

import yaml

from src.config import DockerConfig
from src.optimization.config import ContainerConfig, ModelConfig
from src.optimization.hpc.config import HPCConfig


@dataclass(frozen=True)
class SWEVerifiedPCEConfig:
    config_path: Path
    dataset_snapshot: Path
    image_manifest: Path
    selection_manifest: Path | None
    instance_ids: tuple[str, ...]
    run_dir: Path
    plan: ModelConfig
    code: ModelConfig
    docker: DockerConfig
    container: ContainerConfig
    hpc: HPCConfig
    plan_prompt: str
    plan_instance_template: str
    code_prompt: str
    code_instance_template: str
    nrpv_block: str
    slurm_evaluator_timeout_outcome: str


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _model(value: dict[str, Any], *, temperature: float) -> ModelConfig:
    model = ModelConfig(
        model=str(value["model"]),
        api_base=str(value["api_base"]),
        api_key_env=str(value.get("api_key_env", "DEEPSEEK_API_KEY")),
        temperature=float(value.get("temperature", temperature)),
        max_steps=int(value.get("max_steps", 0)),
        cost_limit=float(value.get("cost_limit", 0.0)),
        timeout=int(value.get("timeout", 1800)),
        max_attempts=int(value.get("max_attempts", 3)),
    )
    if model.max_steps != 0 or model.cost_limit != 0.0:
        raise ValueError("SWE-Verified Agent step and cost limits must be disabled")
    if model.max_attempts != 3:
        raise ValueError("SWE-Verified Agent task attempts must be three")
    return model


def load_swe_verified_pce_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> SWEVerifiedPCEConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "swe_verified_pce":
        raise ValueError("SWE-Verified PCE config requires mode: swe_verified_pce")
    root = (
        config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    )

    def resolve(value: str) -> Path:
        candidate = Path(os.path.expandvars(value)).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    paths = _mapping(raw.get("paths"), "paths")
    plan = _model(_mapping(raw.get("plan"), "plan"), temperature=0.0)
    code = _model(_mapping(raw.get("code"), "code"), temperature=0.0)
    for model in (plan, code):
        if require_api_keys and not os.environ.get(model.api_key_env):
            raise ValueError(f"environment variable {model.api_key_env} is not set")
    prompt_source = paths.get("prompt_source_config")
    if prompt_source:
        prompt_raw = (
            yaml.safe_load(resolve(str(prompt_source)).read_text(encoding="utf-8"))
            or {}
        )
        prompts = _mapping(prompt_raw.get("prompts"), "prompt source prompts")
    else:
        prompts = _mapping(raw.get("prompts"), "prompts")
    container_raw = _mapping(raw.get("container"), "container")
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
        raise ValueError("SWE-Verified PCE supports only Apptainer")
    if "max_running_array_tasks" in hpc_raw or "array_concurrency" in hpc_raw:
        raise ValueError("SWE-Verified PCE leaves array concurrency to Slurm")
    defaults = HPCConfig()
    hpc = HPCConfig(
        submit=bool(hpc_raw.get("submit", False)),
        remote_project_dir=str(
            hpc_raw.get("remote_project_dir", defaults.remote_project_dir)
        ),
        remote_task_dir=str(hpc_raw.get("remote_task_dir", defaults.remote_task_dir)),
        remote_env_file=str(hpc_raw.get("remote_env_file", defaults.remote_env_file)),
        ulhpc_config=str(hpc_raw.get("ulhpc_config", defaults.ulhpc_config)),
        partition=str(hpc_raw.get("partition", defaults.partition)),
        cpus_per_task=int(hpc_raw.get("cpus_per_task", 1)),
        mem=str(hpc_raw.get("mem", "4G")),
        time=str(hpc_raw.get("time", "00:45:00")),
        poll_interval_seconds=int(hpc_raw.get("poll_interval_seconds", 300)),
        task_output_grace_seconds=int(hpc_raw.get("task_output_grace_seconds", 300)),
        missing_task_grace_seconds=int(hpc_raw.get("missing_task_grace_seconds", 600)),
        max_task_attempts=int(hpc_raw.get("max_task_attempts", 3)),
        python_module=str(hpc_raw.get("python_module", defaults.python_module)),
        container_module=str(
            hpc_raw.get("container_module", defaults.container_module)
        ),
        python_bin=str(hpc_raw.get("python_bin", defaults.python_bin)),
        job_name_prefix=str(hpc_raw.get("job_name_prefix", "swe-verified-pce")),
        worker_config_path=str(hpc_raw.get("worker_config_path", str(config_path))),
    )
    if hpc.max_task_attempts != 3:
        raise ValueError("SWE-Verified PCE requires exactly three total attempts")
    if hpc.cpus_per_task != 1 or hpc.mem != "4G":
        raise ValueError("SWE-Verified PCE workers must remain 1 CPU / 4G")
    if hpc.time != "00:45:00":
        raise ValueError("SWE-Verified PCE workers require a 45-minute walltime")

    slurm_evaluator_timeout_outcome = str(
        evaluator_raw.get("slurm_timeout_outcome", "")
    )
    if slurm_evaluator_timeout_outcome != "unknown":
        raise ValueError("SWE-Verified Slurm evaluator exhaustion must remain unknown")

    selection_manifest = (
        resolve(str(paths["selection_manifest"]))
        if paths.get("selection_manifest")
        else None
    )
    instance_ids: tuple[str, ...] = ()
    if selection_manifest is not None:
        selection = json.loads(selection_manifest.read_text(encoding="utf-8"))
        selected = selection.get("selected_instance_ids")
        if (
            selection.get("schema_version") != 1
            or not isinstance(selected, list)
            or not selected
        ):
            raise ValueError(
                "selection manifest requires schema_version 1 and selected_instance_ids"
            )
        instance_ids = tuple(str(value) for value in selected)
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("selected instance IDs must be unique")

    return SWEVerifiedPCEConfig(
        config_path=config_path,
        dataset_snapshot=resolve(str(paths["dataset_snapshot"])),
        image_manifest=resolve(str(paths["image_manifest"])),
        selection_manifest=selection_manifest,
        instance_ids=instance_ids,
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
        hpc=hpc,
        plan_prompt=str(prompts["plan_system"]),
        plan_instance_template=str(prompts["plan_instance"]),
        code_prompt=str(prompts["code_system"]),
        code_instance_template=str(prompts["code_instance"]),
        nrpv_block=str(prompts.get("nrpv_block", "")),
        slurm_evaluator_timeout_outcome=slurm_evaluator_timeout_outcome,
    )
