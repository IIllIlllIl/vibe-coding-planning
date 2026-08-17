"""Configuration for the independent PolyBench PCCE workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any

import yaml

from src.optimization.config import OptimizationConfig, load_optimization_config
from src.optimization.hpc.config import HPCConfig
from src.polybench_pce.config import PolyBenchPCEConfig, load_polybench_pce_config


@dataclass(frozen=True)
class PolyBenchPCCEConfig:
    config_path: Path
    source_snapshot: Path
    image_manifest: Path
    validation_snapshot: Path
    validation_file: str
    pce_outcomes: Path
    guideline_path: Path
    guideline_label: str
    checker_prompt: str
    checker_instance_template: str
    plan_revision_prompt: str
    plan_revision_instance_template: str
    run_dir: Path
    max_review_rejections: int
    instance_ids: tuple[str, ...]
    pce: PolyBenchPCEConfig
    checker: OptimizationConfig
    hpc: HPCConfig


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_polybench_pcce_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> PolyBenchPCCEConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "polybench_pcce":
        raise ValueError("PolyBench PCCE config requires mode: polybench_pcce")
    root = (
        config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    )

    def resolve(value: str) -> Path:
        candidate = Path(os.path.expandvars(value)).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    paths = _mapping(raw.get("paths"), "paths")
    method = _mapping(raw.get("pcce"), "pcce")
    hpc_raw = _mapping(raw.get("hpc"), "hpc")
    prompts = _mapping(raw.get("prompts"), "prompts")
    pce_config_path = resolve(str(paths["pce_runtime_config"]))
    checker_config_path = resolve(str(paths["checker_runtime_config"]))
    pce = load_polybench_pce_config(
        pce_config_path,
        require_api_keys=require_api_keys,
    )
    checker = load_optimization_config(
        checker_config_path,
        require_api_keys=require_api_keys,
    )
    if pce.container.runtime != "apptainer" or checker.container.runtime != "apptainer":
        raise ValueError("PolyBench PCCE requires Apptainer PCE and Checker runtimes")
    if pce.container.sif_cache_dir != checker.container.sif_cache_dir:
        raise ValueError("PCE and Checker must use the same frozen SIF cache")
    if checker.execution.backend != "hpc_slurm":
        raise ValueError("PolyBench PCCE requires an hpc_slurm Checker runtime")

    defaults = HPCConfig()
    if "max_running_array_tasks" in hpc_raw or "array_concurrency" in hpc_raw:
        raise ValueError("PCCE leaves task concurrency entirely to Slurm")
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
        time=str(hpc_raw.get("time", "02:05:00")),
        poll_interval_seconds=int(hpc_raw.get("poll_interval_seconds", 300)),
        task_output_grace_seconds=int(hpc_raw.get("task_output_grace_seconds", 300)),
        missing_task_grace_seconds=int(hpc_raw.get("missing_task_grace_seconds", 600)),
        max_task_attempts=int(hpc_raw.get("max_task_attempts", 3)),
        python_module=str(hpc_raw.get("python_module", defaults.python_module)),
        container_module=str(
            hpc_raw.get("container_module", defaults.container_module)
        ),
        python_bin=str(hpc_raw.get("python_bin", defaults.python_bin)),
        job_name_prefix=str(hpc_raw.get("job_name_prefix", "polybench-pcce")),
        worker_config_path=str(hpc_raw.get("worker_config_path", str(config_path))),
    )
    if hpc.cpus_per_task != 1 or hpc.mem != "4G":
        raise ValueError("PolyBench PCCE workers must remain 1 CPU / 4G")
    if hpc.max_task_attempts < 1:
        raise ValueError("hpc.max_task_attempts must be positive")
    max_rejections = int(method.get("max_review_rejections", 3))
    if max_rejections != 3:
        raise ValueError("the current PCCE method requires exactly three rejections")
    validation_file = str(method.get("validation_file", "validation.jsonl"))
    if Path(validation_file).name != validation_file:
        raise ValueError("pcce.validation_file must be a file name")
    selected_raw = method.get("instance_ids", [])
    if not isinstance(selected_raw, list):
        raise ValueError("pcce.instance_ids must be a list")
    instance_ids = tuple(str(item) for item in selected_raw)
    if len(set(instance_ids)) != len(instance_ids):
        raise ValueError("pcce.instance_ids must be unique")

    run_dir = resolve(str(paths["run_dir"]))
    source_snapshot = resolve(str(paths["source_snapshot"]))
    image_manifest = resolve(str(paths["image_manifest"]))
    pce = replace(
        pce,
        dataset_snapshot=source_snapshot,
        image_manifest=image_manifest,
        run_dir=run_dir,
        hpc=hpc,
    )
    checker = replace(checker, run_dir=run_dir, hpc=hpc)
    return PolyBenchPCCEConfig(
        config_path=config_path,
        source_snapshot=source_snapshot,
        image_manifest=image_manifest,
        validation_snapshot=resolve(str(paths["validation_snapshot"])),
        validation_file=validation_file,
        pce_outcomes=resolve(str(paths["pce_outcomes"])),
        guideline_path=resolve(str(paths["guideline"])),
        guideline_label=str(method["guideline_label"]),
        checker_prompt=str(prompts["checker_system"]),
        checker_instance_template=str(prompts["checker_instance"]),
        plan_revision_prompt=str(prompts["plan_revision_system"]),
        plan_revision_instance_template=str(prompts["plan_revision_instance"]),
        run_dir=run_dir,
        max_review_rejections=max_rejections,
        instance_ids=instance_ids,
        pce=pce,
        checker=checker,
        hpc=hpc,
    )
