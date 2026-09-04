"""Run one label-free Behavioral Checker as a Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from src.optimization.audit import text_sha256
from src.optimization.behavioral_models import (
    BehavioralGEPACase,
    BehavioralRepositoryProxy,
)
from src.optimization.behavioral_runtime import BehavioralLocalChecker
from src.optimization.config import OfflineExecutionConfig, load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


def _manifest_path(manifest_path: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else manifest_path.parent / path


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    rules = ""
    failure_stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("mode") != "behavioral_checker":
            raise ValueError("worker manifest mode is not behavioral_checker")
        worker = dict(manifest["worker_payload"])
        if set(worker) != {
            "task_semantics",
            "checker_input",
            "repository_materialization",
        }:
            raise ValueError("Behavioral worker manifest has an invalid boundary")
        checker = dict(worker["checker_input"])
        if set(checker) != {
            "pre_p1_context",
            "proposed_plan_p1",
            "repository_proxy",
        }:
            raise ValueError("Behavioral Checker input has an invalid boundary")
        repository = dict(checker["repository_proxy"])
        materialization = dict(worker["repository_materialization"])
        rules = _manifest_path(task_manifest_path, manifest["rules_path"]).read_text(
            encoding="utf-8"
        )
        failure_stage = "config_load"
        config = load_optimization_config(config_path)
        mirror = Path(str(materialization["mirror_path"])).resolve()
        repository_root = config.behavioral_repository.repositories_root.resolve()
        mirror_relpath = mirror.relative_to(repository_root).as_posix()
        if materialization["proxy_commit"] != repository["proxy_commit"]:
            raise ValueError("Behavioral proxy commit mismatch")
        case = BehavioralGEPACase(
            instance_id=str(manifest["instance_id"]),
            split=str(manifest["split"]),
            # Supervision is deliberately absent from the worker manifest.
            # This placeholder is never rendered or used by the Checker.
            decision="DO_NOT_ACCEPT",
            confidence="worker_placeholder",
            signal="worker_placeholder",
            pre_p1_context=tuple(checker["pre_p1_context"]),
            proposed_plan_p1=str(checker["proposed_plan_p1"]),
            repository=BehavioralRepositoryProxy(
                repo=str(repository["repo"]),
                proxy_commit=str(repository["proxy_commit"]),
                instance_id=str(repository["instance_id"]),
                state_semantics=str(repository["state_semantics"]),
                conflict_authority=str(repository["conflict_authority"]),
            ),
            reflection_evidence={},
            audit_provenance={"mirror_relpath": mirror_relpath},
            repetition_index=(
                int(manifest["repetition_index"])
                if manifest.get("repetition_index") is not None
                else None
            ),
        )
        config = replace(
            config,
            run_dir=attempt_dir,
            execution=OfflineExecutionConfig(backend="local"),
            checker=replace(config.checker, max_attempts=1),
        )
        failure_stage = "checker_execution"
        output = BehavioralLocalChecker(config)(case, rules)
        failure_stage = "output_write"
        completed = {
            "status": "completed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "behavioral_checker",
            "fingerprint": manifest["fingerprint"],
            "instance_id": case.instance_id,
            "split": case.split,
            "candidate_sha256": text_sha256(rules),
            "checker_output": output.to_dict(include_trajectory=True),
        }
        if case.repetition_index is not None:
            completed["repetition_index"] = case.repetition_index
        atomic_json(output_path, completed)
        return 0
    except Exception as exc:
        failure = {
            "status": "agent_failed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "behavioral_checker",
            "fingerprint": manifest.get("fingerprint"),
            "instance_id": manifest.get("instance_id"),
            "candidate_sha256": text_sha256(rules),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failure_stage": failure_stage,
            "failure_kind": "operational",
        }
        if manifest.get("repetition_index") is not None:
            failure["repetition_index"] = manifest["repetition_index"]
        atomic_json(attempt_dir / "failure.json", failure)
        atomic_json(output_path, failure)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    args = parser.parse_args()
    return run_task(
        config_path=args.config,
        task_manifest_path=args.task_manifest,
        output_path=args.output,
        attempt_dir=args.attempt_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
