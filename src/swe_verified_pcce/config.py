"""Configuration for the independent SWE-Verified PCCE workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any

import yaml

from src.optimization.config import OptimizationConfig, load_optimization_config
from src.optimization.hpc.config import HPCConfig
from src.swe_verified_pce.config import (
    SWEVerifiedPCEConfig,
    load_swe_verified_pce_config,
)


@dataclass(frozen=True)
class PCCEPhaseTimes:
    first_review: str
    revision_review: str
    ce: str


@dataclass(frozen=True)
class SWEVerifiedPCCEConfig:
    config_path: Path
    source_snapshot: Path
    image_manifest: Path
    pce_outcomes: Path
    selection_manifest: Path
    guideline_path: Path
    guideline_label: str
    checker_prompt: str
    checker_instance_template: str
    plan_revision_prompt: str
    plan_revision_instance_template: str
    run_dir: Path
    max_review_rejections: int
    instance_ids: tuple[str, ...]
    pce: SWEVerifiedPCEConfig
    checker: OptimizationConfig
    hpc: HPCConfig
    phase_times: PCCEPhaseTimes
    execution_mode: str


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def load_swe_verified_pcce_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> SWEVerifiedPCCEConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "swe_verified_pcce":
        raise ValueError("SWE-Verified PCCE config requires mode: swe_verified_pcce")
    root = (
        config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    )

    def resolve(value: str) -> Path:
        candidate = Path(os.path.expandvars(value)).expanduser()
        return candidate if candidate.is_absolute() else root / candidate

    paths = _mapping(raw.get("paths"), "paths")
    method = _mapping(raw.get("pcce"), "pcce")
    checker_overrides = _mapping(raw.get("checker"), "checker")
    prompt_source = paths.get("prompt_source_config")
    if prompt_source:
        prompt_raw = (
            yaml.safe_load(resolve(str(prompt_source)).read_text(encoding="utf-8"))
            or {}
        )
        prompts = _mapping(prompt_raw.get("prompts"), "prompt source prompts")
    else:
        prompts = _mapping(raw.get("prompts"), "prompts")
    hpc_raw = _mapping(raw.get("hpc"), "hpc")
    phase_raw = _mapping(hpc_raw.get("phase_times"), "hpc.phase_times")
    pce = load_swe_verified_pce_config(
        resolve(str(paths["pce_runtime_config"])),
        require_api_keys=require_api_keys,
    )
    checker = load_optimization_config(
        resolve(str(paths["checker_runtime_config"])),
        require_api_keys=require_api_keys,
    )
    if pce.container.runtime != "apptainer" or checker.container.runtime != "apptainer":
        raise ValueError("SWE-Verified PCCE requires Apptainer runtimes")
    if pce.container.sif_cache_dir != checker.container.sif_cache_dir:
        raise ValueError("PCE and Checker must use the same SIF cache")
    if checker.execution.backend != "hpc_slurm":
        raise ValueError("SWE-Verified PCCE requires an hpc_slurm Checker runtime")
    if "max_running_array_tasks" in hpc_raw or "array_concurrency" in hpc_raw:
        raise ValueError("SWE-Verified PCCE leaves array concurrency to Slurm")

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
        time=str(phase_raw["first_review"]),
        poll_interval_seconds=int(hpc_raw.get("poll_interval_seconds", 300)),
        task_output_grace_seconds=int(hpc_raw.get("task_output_grace_seconds", 300)),
        missing_task_grace_seconds=int(hpc_raw.get("missing_task_grace_seconds", 600)),
        max_task_attempts=int(hpc_raw.get("max_task_attempts", 3)),
        python_module=str(hpc_raw.get("python_module", defaults.python_module)),
        container_module=str(
            hpc_raw.get("container_module", defaults.container_module)
        ),
        python_bin=str(hpc_raw.get("python_bin", defaults.python_bin)),
        job_name_prefix=str(hpc_raw.get("job_name_prefix", "swe-verified-pcce")),
        worker_config_path=str(hpc_raw.get("worker_config_path", str(config_path))),
    )
    if hpc.cpus_per_task != 1 or hpc.mem != "4G":
        raise ValueError("SWE-Verified PCCE workers must remain 1 CPU / 4G")
    if hpc.max_task_attempts != 3:
        raise ValueError("SWE-Verified PCCE requires exactly three total attempts")
    phase_times = PCCEPhaseTimes(
        first_review=str(phase_raw["first_review"]),
        revision_review=str(phase_raw["revision_review"]),
        ce=str(phase_raw["ce"]),
    )
    if (
        phase_times.first_review,
        phase_times.revision_review,
        phase_times.ce,
    ) != ("00:45:00", "00:45:00", "00:45:00"):
        raise ValueError("every SWE-Verified PCCE phase requires a 45-minute walltime")

    max_rejections = int(method.get("max_review_rejections", 3))
    if max_rejections != 3:
        raise ValueError("SWE-Verified full PCCE requires three review rejections")
    selection_manifest = resolve(str(paths["selection_manifest"]))
    selection = json.loads(selection_manifest.read_text(encoding="utf-8"))
    selected = selection.get("selected_instance_ids")
    if (
        selection.get("schema_version") != 1
        or not isinstance(selected, list)
        or not selected
    ):
        raise ValueError("selection manifest requires selected_instance_ids")
    instance_ids = tuple(str(value) for value in selected)
    if len(set(instance_ids)) != len(instance_ids):
        raise ValueError("selected instance IDs must be unique")

    run_dir = resolve(str(paths["run_dir"]))
    source_snapshot = resolve(str(paths["source_snapshot"]))
    image_manifest = resolve(str(paths["image_manifest"]))
    pce = replace(
        pce,
        dataset_snapshot=source_snapshot,
        image_manifest=image_manifest,
        selection_manifest=selection_manifest,
        instance_ids=instance_ids,
        run_dir=run_dir,
        hpc=hpc,
    )
    checker = replace(
        checker,
        run_dir=run_dir,
        hpc=hpc,
        checker=replace(
            checker.checker,
            max_steps=int(checker_overrides["max_steps"]),
            cost_limit=float(checker_overrides["cost_limit"]),
            agent_timeout_seconds=int(checker_overrides["agent_timeout_seconds"]),
            max_attempts=int(checker_overrides["max_attempts"]),
        ),
    )
    if (
        checker.checker.max_steps != 0
        or checker.checker.cost_limit != 0.0
        or checker.checker.agent_timeout_seconds != 0
        or checker.checker.max_attempts != hpc.max_task_attempts
    ):
        raise ValueError("SWE-Verified Checker limits must defer to Slurm attempts")
    revision_system = str(prompts.get("plan_revision_system", ""))
    revision_instance = str(prompts.get("plan_revision_instance", ""))
    if not revision_system or not revision_instance:
        raise ValueError("PCCE requires both plan-revision prompts")

    return SWEVerifiedPCCEConfig(
        config_path=config_path,
        source_snapshot=source_snapshot,
        image_manifest=image_manifest,
        pce_outcomes=resolve(str(paths["pce_outcomes"])),
        selection_manifest=selection_manifest,
        guideline_path=resolve(str(paths["guideline"])),
        guideline_label=str(method["guideline_label"]),
        checker_prompt=str(prompts["checker_system"]),
        checker_instance_template=str(prompts["checker_instance"]),
        plan_revision_prompt=revision_system,
        plan_revision_instance_template=revision_instance,
        run_dir=run_dir,
        max_review_rejections=max_rejections,
        instance_ids=instance_ids,
        pce=pce,
        checker=checker,
        hpc=hpc,
        phase_times=phase_times,
        execution_mode="full_pcce",
    )
