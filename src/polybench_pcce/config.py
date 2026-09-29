"""Configuration for the independent PolyBench PCCE workflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from typing import Any

import yaml

from src.optimization.config import OptimizationConfig, load_optimization_config
from src.optimization.hpc.config import HPCConfig
from src.polybench_pce.config import (
    PCEExecutionConfig,
    PolyBenchPCEConfig,
    load_polybench_pce_config,
)


@dataclass(frozen=True)
class PolyBenchPCCEConfig:
    config_path: Path
    source_snapshot: Path
    image_manifest: Path
    validation_snapshot: Path | None
    validation_file: str
    pce_outcomes: Path
    selection_manifest: Path | None
    guideline_path: Path
    guideline_label: str
    checker_prompt: str
    checker_instance_template: str
    plan_revision_prompt: str
    plan_revision_instance_template: str
    run_dir: Path
    execution_mode: str
    max_review_rejections: int
    instance_ids: tuple[str, ...]
    pce: PolyBenchPCEConfig
    checker: OptimizationConfig | None
    hpc: HPCConfig
    gate_config_path: Path | None = None
    dialogue_checker_prompt: str = ""
    dialogue_checker_instance_template: str = ""


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
    execution_mode = str(method.get("execution_mode", "full_pcce"))
    if execution_mode not in {"full_pcce", "checker_only", "ace_pcce", "sampled_pcce"}:
        raise ValueError("unsupported pcce.execution_mode")
    hpc_raw = _mapping(raw.get("hpc"), "hpc")
    runtime_raw = _mapping(raw.get("runtime", {}), "runtime")
    gate_config_path: Path | None = None
    gate = None
    if execution_mode == "sampled_pcce":
        from src.polybench_pcce.paired_gate import load_paired_gate_config

        gate_config_path = resolve(str(paths["gate_config"])).resolve()
        gate = load_paired_gate_config(gate_config_path)
        gate_raw = yaml.safe_load(gate_config_path.read_text(encoding="utf-8"))
        prompt_raw = yaml.safe_load(
            resolve(str(gate_raw["inputs"]["prompt_bundle"])).read_text(encoding="utf-8")
        )
        prompts = _mapping(prompt_raw, "paired gate Checker prompts")
        if set(prompts) != {"checker_system", "checker_instance"}:
            raise ValueError("sampled PCCE requires the frozen paired gate Checker prompts")
        if "prompt_source_config" in paths or "checker_runtime_config" in paths:
            raise ValueError("sampled PCCE takes Checker authority only from gate_config")
        pce_config_path = gate.pce_runtime_config
    elif paths.get("prompt_source_config"):
        prompt_source = paths["prompt_source_config"]
        prompt_raw = (
            yaml.safe_load(resolve(str(prompt_source)).read_text(encoding="utf-8"))
            or {}
        )
        prompts = _mapping(prompt_raw.get("prompts"), "prompt source prompts")
    else:
        prompts = _mapping(raw.get("prompts"), "prompts")
    if gate is None:
        pce_config_path = resolve(str(paths["pce_runtime_config"]))
    pce = load_polybench_pce_config(
        pce_config_path,
        require_api_keys=require_api_keys,
    )
    checker = (
        None if gate is not None else load_optimization_config(
            resolve(str(paths["checker_runtime_config"])),
            require_api_keys=require_api_keys,
        )
    )
    if runtime_raw:
        pce = replace(
            pce,
            execution=PCEExecutionConfig(
                code_phase_timeout_seconds=int(
                    runtime_raw.get(
                        "code_phase_timeout_seconds",
                        pce.execution.code_phase_timeout_seconds,
                    )
                ),
                repository_command_timeout_seconds=int(
                    runtime_raw.get(
                        "repository_command_timeout_seconds",
                        pce.execution.repository_command_timeout_seconds,
                    )
                ),
            ),
        )
        if checker is not None:
            checker = replace(
                checker,
                checker=replace(
                    checker.checker,
                    max_steps=int(runtime_raw.get("checker_max_steps", checker.checker.max_steps)),
                    cost_limit=float(runtime_raw.get("checker_cost_limit", checker.checker.cost_limit)),
                ),
            )
    if pce.execution.code_phase_timeout_seconds < 0:
        raise ValueError("runtime.code_phase_timeout_seconds must be non-negative")
    if pce.execution.repository_command_timeout_seconds < 1:
        raise ValueError(
            "runtime.repository_command_timeout_seconds must be positive"
        )
    if checker is not None and (checker.checker.max_steps < 0 or checker.checker.cost_limit < 0):
        raise ValueError("runtime Checker limits must be non-negative")
    if pce.container.runtime != "apptainer" or (checker is not None and checker.container.runtime != "apptainer"):
        raise ValueError("PolyBench PCCE requires Apptainer PCE and Checker runtimes")
    if checker is not None and pce.container.sif_cache_dir != checker.container.sif_cache_dir:
        raise ValueError("PCE and Checker must use the same frozen SIF cache")
    if checker is not None and checker.execution.backend != "hpc_slurm":
        raise ValueError("PolyBench PCCE requires an hpc_slurm Checker runtime")
    if gate is not None and pce.container.sif_cache_dir != Path(
        os.path.expandvars(str(gate_raw["container"]["sif_cache_dir"]))
    ).expanduser():
        raise ValueError("PCE and paired gate must use the same SIF cache")

    defaults = HPCConfig()
    if ("max_running_array_tasks" in hpc_raw or "array_concurrency" in hpc_raw) and gate is None:
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
        max_running_array_tasks=int(hpc_raw.get("max_running_array_tasks", 0)),
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
    expected_rejections = 1 if execution_mode == "checker_only" else 3
    if max_rejections != expected_rejections:
        raise ValueError(
            f"{execution_mode} requires max_review_rejections: {expected_rejections}"
        )
    validation_file = str(method.get("validation_file", "validation.jsonl"))
    if Path(validation_file).name != validation_file:
        raise ValueError("pcce.validation_file must be a file name")
    selection_manifest = (
        resolve(str(paths["selection_manifest"]))
        if paths.get("selection_manifest") and gate is None
        else None
    )
    selected_raw = method.get("instance_ids", [])
    if not isinstance(selected_raw, list):
        raise ValueError("pcce.instance_ids must be a list")
    if selection_manifest is not None:
        if selected_raw:
            raise ValueError(
                "selection_manifest and inline pcce.instance_ids are mutually exclusive"
            )
        selection = json.loads(selection_manifest.read_text(encoding="utf-8"))
        if selection.get("schema_version") != 1:
            raise ValueError("selection manifest must use schema_version: 1")
        selected_raw = selection.get("selected_instance_ids")
        if not isinstance(selected_raw, list) or not selected_raw:
            raise ValueError("selection manifest requires selected_instance_ids")
    instance_ids = tuple(str(item) for item in selected_raw)
    if len(set(instance_ids)) != len(instance_ids):
        raise ValueError("pcce.instance_ids must be unique")

    run_dir = resolve(str(paths["run_dir"]))
    source_snapshot = gate.source_snapshot if gate is not None else resolve(str(paths["source_snapshot"]))
    image_manifest = gate.image_manifest if gate is not None else resolve(str(paths["image_manifest"]))
    pce = replace(
        pce,
        dataset_snapshot=source_snapshot,
        image_manifest=image_manifest,
        run_dir=run_dir,
        hpc=hpc,
    )
    if checker is not None:
        checker = replace(checker, run_dir=run_dir, hpc=hpc)
    plan_revision_prompt = pce.plan_prompt if gate is not None else str(prompts.get("plan_revision_system", ""))
    plan_revision_instance = pce.plan_instance_template if gate is not None else str(prompts.get("plan_revision_instance", ""))
    dialogue_checker_prompt = str(prompts.get("dialogue_checker_system", ""))
    dialogue_checker_instance = str(prompts.get("dialogue_checker_instance", ""))
    if execution_mode in {"full_pcce", "ace_pcce"} and (
        not plan_revision_prompt or not plan_revision_instance
    ):
        raise ValueError(f"{execution_mode} requires both plan-revision prompts")
    if execution_mode == "ace_pcce" and (
        not dialogue_checker_prompt or not dialogue_checker_instance
    ):
        raise ValueError("ace_pcce requires both dialogue-Checker prompts")
    if gate is not None and (
        pce.plan_submission_protocol != "direct_human_markdown_v5"
        or pce.plan.temperature != 1.0
        or pce.plan.thinking != "disabled"
    ):
        raise ValueError("sampled PCCE requires the frozen PCE Planner settings")
    return PolyBenchPCCEConfig(
        config_path=config_path,
        source_snapshot=source_snapshot,
        image_manifest=image_manifest,
        validation_snapshot=(resolve(str(paths["validation_snapshot"])) if gate is None else None),
        validation_file=validation_file,
        pce_outcomes=(resolve(str(paths["pce_outcomes"])) if gate is None else gate.pce_outcomes),
        selection_manifest=selection_manifest,
        guideline_path=(resolve(str(paths["guideline"])) if gate is None else gate.guideline),
        guideline_label=str(method["guideline_label"]),
        checker_prompt=str(prompts["checker_system"]),
        checker_instance_template=str(prompts["checker_instance"]),
        plan_revision_prompt=plan_revision_prompt,
        plan_revision_instance_template=plan_revision_instance,
        dialogue_checker_prompt=dialogue_checker_prompt,
        dialogue_checker_instance_template=dialogue_checker_instance,
        run_dir=run_dir,
        execution_mode=execution_mode,
        max_review_rejections=max_rejections,
        instance_ids=instance_ids,
        pce=pce,
        checker=checker,
        hpc=hpc,
        gate_config_path=gate_config_path,
    )
