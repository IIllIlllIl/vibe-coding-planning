"""Run exactly one Offline initial-Reflection or repair Agent task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import FatalError
from src.optimization.config import (
    OfflineExecutionConfig,
    load_optimization_config,
)
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.audit import JsonlLogger
from src.optimization.reflection import (
    MiniSWEReflectionProposer,
    ReflectionRepairRequired,
    run_reflection_contamination_repair,
)


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, MemoryError):
        return "memory"
    if isinstance(exc, FatalError):
        return "fatal"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io"
    if isinstance(exc, ValueError):
        return "validation"
    if isinstance(exc, RuntimeError):
        return "runtime"
    return "unexpected"


def _manifest_path(manifest_path: Path, value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return manifest_path.parent / path


def run_task(
    *,
    config_path: Path,
    manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    failure_stage = "input_load"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        failure_stage = "config_load"
        config = load_optimization_config(config_path)
        config = replace(
            config,
            run_dir=attempt_dir,
            execution=OfflineExecutionConfig(backend="local"),
        )
        failure_stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        mode = str(manifest.get("mode"))
        if mode == "offline_reflection":
            failure_stage = "reflection_execution"
            proposer = MiniSWEReflectionProposer(
                config,
                capacity,
                defer_contamination_repair=True,
            )
            try:
                proposal = proposer(
                    dict(manifest["candidate"]),
                    dict(manifest["reflective_dataset"]),
                    list(manifest["components_to_update"]),
                )
            except ReflectionRepairRequired as required:
                atomic_json(
                    output_path,
                    {
                        "status": "completed",
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "mode": mode,
                        "fingerprint": manifest["fingerprint"],
                        "outcome": "repair_required",
                        "proposed_rules": required.proposed_rules,
                        "contamination_hits": required.contamination_hits,
                        "evidence_bundle": str(required.bundle_path),
                        "instance_ids": required.instance_ids,
                    },
                )
                return 0
        elif mode == "offline_reflection_repair":
            failure_stage = "repair_input_load"
            source = json.loads(
                _manifest_path(
                    manifest_path,
                    manifest["source_manifest"],
                ).read_text(
                    encoding="utf-8"
                )
            )
            records = list(source["reflective_dataset"]["rules"])
            failure_stage = "reflection_repair_execution"
            repair = run_reflection_contamination_repair(
                config,
                capacity,
                bundle=_manifest_path(
                    manifest_path,
                    manifest["evidence_bundle"],
                ),
                trajectory_dir=attempt_dir,
                parent_rules=str(source["candidate"]["rules"]),
                proposed_rules=str(manifest["proposed_rules"]),
                contamination_hits=list(manifest["contamination_hits"]),
                records=records,
                instance_ids=list(manifest["instance_ids"]),
                audit=JsonlLogger(attempt_dir / "audit_events.jsonl"),
                usage=JsonlLogger(attempt_dir / "usage.jsonl"),
            )
            proposal = {"rules": str(repair["rules"])}
        else:
            raise ValueError(f"unknown Offline Reflection worker mode: {mode}")
        failure_stage = "output_write"
        atomic_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "fingerprint": manifest["fingerprint"],
                "outcome": "proposal",
                "proposal": proposal,
            },
        )
        return 0
    except Exception as exc:
        failure = {
            "status": (
                "blocking_failed"
                if isinstance(exc, (FatalError, MemoryError))
                else "agent_failed"
            ),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": manifest.get("mode"),
            "fingerprint": manifest.get("fingerprint"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failure_stage": failure_stage,
            "failure_category": _failure_category(exc),
        }
        atomic_json(attempt_dir / "failure.json", failure)
        atomic_json(output_path, failure)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    args = parser.parse_args()
    return run_task(
        config_path=args.config,
        manifest_path=args.manifest,
        output_path=args.output,
        attempt_dir=args.attempt_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
