"""Prepare and submit the frozen two-case Behavioral context-limit smoke."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

import yaml

from src.optimization.audit import text_sha256
from src.optimization.behavioral_dataset import load_behavioral_snapshot
from src.optimization.behavioral_hpc_executor import (
    HPCSlurmBehavioralCheckerExecutor,
    behavioral_evaluation_fingerprint,
    build_behavioral_checker_array_script,
)
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


IDENTITY = "behavioral-extreme-context-smoke-v1-20260830"
TRAIN_CASE_ID = "b2b5366b-6963-4be6-be4a-ce094982febd#first-plan"
VALIDATION_CASE_ID = "cf4618ba-7f51-4a75-bef9-5fa32a9f003b#first-plan"
CASE_IDS = (TRAIN_CASE_ID, VALIDATION_CASE_ID)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_config(base_path: Path, run_dir: Path) -> Path:
    raw = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    raw["experiment_contract"] = {
        "identity": IDENTITY,
        "status": "launch_authorized",
        "purpose": "two_case_checker_context_limit_smoke",
        "calls": {"checker": 2, "reflection": 0, "gepa_optimize": 0},
        "case_ids": list(CASE_IDS),
        "operational_failure_is_a_decision": False,
    }
    raw["paths"]["run_dir"] = str(run_dir)
    raw["hpc"].update(
        {
            "submit": True,
            "max_task_attempts": 1,
            "cpus_per_task": 1,
            "mem": "4G",
            "time": "00:35:00",
            "job_name_prefix": IDENTITY,
            "worker_config_path": str(run_dir / "runtime_config.yaml"),
        }
    )
    path = run_dir / "runtime_config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _select_cases(config: Any) -> list[Any]:
    train, validation = load_behavioral_snapshot(config.dataset_snapshot)
    by_id = {case.instance_id: case for case in train + validation}
    if set(CASE_IDS) - set(by_id):
        raise ValueError("frozen extreme-context case is absent from the snapshot")
    cases = [by_id[case_id] for case_id in CASE_IDS]
    if [case.split for case in cases] != ["train", "validation"]:
        raise ValueError("extreme-context smoke split identity changed")
    return cases


def _assert_label_free_tasks(batch_dir: Path) -> None:
    forbidden = {
        "observed_decision",
        "observed_accept",
        "decision",
        "score",
        "reflection_evidence",
        "post_boundary_evidence",
    }
    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | {key for child in value.values() for key in keys(child)}
        if isinstance(value, list):
            return {key for child in value for key in keys(child)}
        return set()

    for path in sorted((batch_dir / "tasks").glob("task_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if forbidden & keys(payload):
            raise ValueError(f"Checker task boundary leakage in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--base-config",
        type=Path,
        default=Path(
            "configs/gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml"
        ),
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path(
            "output/SWE-chat/behavioral-extreme-context-smoke-v1-20260830"
        ),
    )
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    runtime_path = _runtime_config(args.base_config.resolve(), run_dir)
    config = load_optimization_config(
        runtime_path, require_api_keys=not args.preflight_only
    )
    cases = _select_cases(config)
    guideline = config.initial_rules_path.read_text(encoding="utf-8").strip()
    executor = HPCSlurmBehavioralCheckerExecutor(config)
    fingerprint = behavioral_evaluation_fingerprint(
        config, batch=cases, rules=guideline, capture_traces=True
    )
    batch_dir = executor.root / fingerprint
    tasks = executor._prepare(
        batch_dir,
        fingerprint=fingerprint,
        batch=cases,
        rules=guideline,
        capture_traces=True,
    )
    script_path = batch_dir / "checker_array_attempt_01.sbatch"
    script_path.write_text(
        build_behavioral_checker_array_script(
            config=config,
            batch_dir=batch_dir,
            task_indices=[task.index for task in tasks],
            attempt=1,
        ),
        encoding="utf-8",
    )
    _assert_label_free_tasks(batch_dir)
    contract = {
        "schema_version": 1,
        "identity": IDENTITY,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_config": str(args.base_config),
        "base_config_sha256": _sha256(args.base_config),
        "runtime_config_sha256": _sha256(runtime_path),
        "dataset_manifest_sha256": _sha256(config.dataset_snapshot / "manifest.json"),
        "candidate_sha256": text_sha256(guideline),
        "fingerprint": fingerprint,
        "case_ids": list(CASE_IDS),
        "calls": {"checker": 2, "reflection": 0, "gepa_optimize": 0},
        "slurm": {
            "array": "0,1",
            "cpus_per_task": 1,
            "mem": "4G",
            "time": "00:35:00",
            "max_attempts": 1,
        },
        "contains_observed_decision_in_worker_manifests": False,
        "contains_post_boundary_evidence_in_worker_manifests": False,
        "status": "prepared" if args.preflight_only else "submitting",
    }
    atomic_json(run_dir / "smoke_contract.json", contract)
    if args.preflight_only:
        print(json.dumps(contract, sort_keys=True))
        return 0

    result = subprocess.run(
        ["sbatch", "--parsable", str(script_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    job_id = result.stdout.strip().split(";", 1)[0]
    if not job_id.isdigit():
        raise RuntimeError(f"unexpected sbatch result: {result.stdout!r}")
    contract.update(
        {
            "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "slurm_array_job_id": job_id,
        }
    )
    atomic_json(run_dir / "smoke_contract.json", contract)
    print(json.dumps(contract, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
