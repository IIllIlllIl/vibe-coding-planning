"""Bind a learned Planner artifact to existing Safe PCE execution components.

No P/C/E controller, worker, retry loop, isolation or checkpoint is copied.
The generated config is an auditable mechanical overlay of a chosen PCE config.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import yaml

from src.agents.planning_artifact import PLANNING_ARTIFACT_INSTRUCTIONS
from src.optimization.behavioral_hybrid import checker_projection


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_pce_config(path: Path, *, require_api_keys=False):
    mode = yaml.safe_load(path.read_text())["mode"]
    if mode == "polybench_pce":
        from src.polybench_pce.config import load_polybench_pce_config
        return mode, load_polybench_pce_config(path, require_api_keys=require_api_keys)
    if mode == "swe_verified_pce":
        from src.swe_verified_pce.config import load_swe_verified_pce_config
        return mode, load_swe_verified_pce_config(path, require_api_keys=require_api_keys)
    raise ValueError("safe_pce_config must reference polybench_pce or swe_verified_pce")


def pce_cases(mode, config):
    if mode == "polybench_pce":
        from src.polybench_pce.dataset import load_polybench_pce_cases
        cases, _, _ = load_polybench_pce_cases(config.dataset_snapshot, config.image_manifest)
    else:
        from src.swe_verified_pce.dataset import load_swe_verified_pce_cases
        cases, _, _ = load_swe_verified_pce_cases(config.dataset_snapshot, config.image_manifest)
    by_id = {case.instance_id: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("Safe PCE source contains duplicate task IDs")
    if config.instance_ids:
        unknown = set(config.instance_ids) - set(by_id)
        if unknown:
            raise ValueError(f"Safe PCE selection contains unavailable tasks: {sorted(unknown)}")
        cases = [by_id[identifier] for identifier in config.instance_ids]
    return cases


def validate_learning_source(config):
    if config.plan_submission_protocol != "direct_human_markdown_v5":
        raise ValueError("Safe PCE learning requires the proven direct_human_markdown_v5 Planner boundary")
    if config.planning_rules:
        raise ValueError("base PCE config must not contain an existing learned artifact")


def pce_identity(path: Path) -> dict:
    """Freeze resolved scientific settings as well as their referenced inputs."""
    mode, config = load_pce_config(path)
    if mode == "polybench_pce":
        from src.polybench_pce.hpc_executor import pce_semantic_sha256
    else:
        from src.swe_verified_pce.hpc_executor import pce_semantic_sha256
    return {"mode": mode, "config_sha256": sha256(path),
            "semantic_sha256": pce_semantic_sha256(config),
            "source_manifest_sha256": sha256(config.dataset_snapshot / "manifest.json"),
            "image_manifest_sha256": sha256(config.image_manifest),
            "selection_sha256": sha256(config.selection_manifest) if config.selection_manifest else None}


def resolved_pce_payload(base):
    """Resolve source references before moving a config; retain all policies."""
    raw = deepcopy(yaml.safe_load(base.config_path.read_text()))
    root = base.config_path.parents[1] if base.config_path.parent.name == "configs" else Path.cwd()
    for key, value in raw["paths"].items():
        if value:
            target = Path(os.path.expandvars(str(value))).expanduser()
            raw["paths"][key] = str(target if target.is_absolute() else root / target)
    for key in ("prompt_source_config", "plan_prompt_source_config", "code_prompt_source_config"):
        raw["paths"].pop(key, None)
    raw["prompts"] = {"plan_system": base.plan_prompt, "plan_instance": base.plan_instance_template,
                      "code_system": base.code_prompt, "code_instance": base.code_instance_template,
                      "nrpv_block": base.nrpv_block}
    return raw


def materialize_pce_config(base, artifact: dict, directory: Path, *, repetitions: int = 1,
                           run_dir: Path | None = None, job_name_prefix: str | None = None):
    """Persist effective prompts/config; candidate data stays a template value.

    References are resolved using the original root, never the generated
    directory. Existing workers reload this exact file.
    """
    raw = resolved_pce_payload(base)
    instance = base.plan_instance_template
    if "{{ planning_rules }}" not in instance and "{{planning_rules}}" not in instance:
        instance += "\n<planning_artifact>{{ planning_rules }}</planning_artifact>\n"
    raw["prompts"] = {"plan_system": base.plan_prompt,
                      "plan_instance": instance + "\n" + PLANNING_ARTIFACT_INSTRUCTIONS,
                      "code_system": base.code_prompt, "code_instance": base.code_instance_template,
                      "nrpv_block": base.nrpv_block}
    raw["planning_rules"] = json.dumps(checker_projection(artifact), ensure_ascii=False)
    # Repetition comes from the optimizer contract, never a historical 77x2
    # selection. All independent physical units remain one existing PCE array.
    if raw["mode"] == "polybench_pce":
        raw["pce"] = {"repetitions": repetitions}
    else:
        raw.setdefault("execution", {}).pop("manifest", None)
        raw["execution"]["pce_runs_per_instance"] = repetitions
    config_path = directory / "effective_pce.yaml"
    raw["paths"]["run_dir"] = str(run_dir or directory / "run")
    raw.setdefault("hpc", {})["worker_config_path"] = str(config_path)
    if job_name_prefix:
        raw["hpc"]["job_name_prefix"] = job_name_prefix
    # Freeze every inherited phase policy; do not change network/resource limits.
    content = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    directory.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and config_path.read_text() != content:
        raise ValueError("effective PCE config identity mismatch; use a fresh run directory")
    if not config_path.exists():
        temporary = config_path.with_suffix(".tmp")
        temporary.write_text(content)
        temporary.replace(config_path)
    return load_pce_config(config_path)[1]


def execute_pce(config, mode: str, cases: list, *, backend: str) -> list[dict]:
    """Dispatch the proven PCE executor, or its real Worker in local test mode."""
    if mode == "polybench_pce":
        from src.polybench_pce.hpc_executor import PolyBenchPCEHPCExecutor, execution_fingerprint
        from src.polybench_pce.worker import run_task
        executor = PolyBenchPCEHPCExecutor(config)
        fingerprint = lambda: execution_fingerprint(config, cases)
    else:
        from src.swe_verified_pce.hpc_executor import SWEVerifiedPCEHPCExecutor
        from src.swe_verified_pce.worker import run_task
        executor = SWEVerifiedPCEHPCExecutor(config)
        fingerprint = lambda: executor.execution_fingerprint(cases)
    if backend == "hpc_slurm":
        return executor.evaluate(cases)
    if backend != "local":
        raise ValueError("unsupported PCE execution backend")
    # Local use is explicit (tests/development), not a fallback from Slurm.
    identity = fingerprint()
    batch_dir = config.run_dir / "hpc_tasks" / "pce" / identity
    tasks = executor._prepare(batch_dir, identity, cases)
    outputs = []
    for task in tasks:
        if not task.output_path.is_file():
            run_task(config_path=config.config_path, task_manifest_path=task.manifest_path,
                     output_path=task.output_path, attempt_dir=task.attempts_dir / "attempt_01",
                     checkpoint_dir=batch_dir / "checkpoints" / f"task_{task.index:04d}", attempt=1)
        value = json.loads(task.output_path.read_text())
        if (value.get("fingerprint") != identity or value.get("instance_id") != task.instance_id
                or value.get("task_index") != task.index):
            raise ValueError("local Safe PCE cached result identity mismatch")
        outputs.append(value)
    return outputs
