"""Run one frozen PolyBench PCE instance as one Slurm array element."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import AgentTaskError, FatalError
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pce.config import load_polybench_pce_config
from src.polybench_pce.evaluator import PolyBenchEvaluatorOperationalError
from src.polybench_pce.models import PolyBenchPCECase
from src.polybench_pce.runner import PolyBenchPCERunner, checkpoint_identity


def _category(exc: BaseException) -> str:
    if isinstance(exc, AgentTaskError):
        return "agent"
    if isinstance(exc, PolyBenchEvaluatorOperationalError):
        return "evaluator"
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
    return "unexpected"


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
    checkpoint_dir: Path,
    attempt: int,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    case: PolyBenchPCECase | None = None
    stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        case = PolyBenchPCECase.from_dict(dict(manifest["case"]))
        stage = "config_load"
        config = load_polybench_pce_config(config_path)
        stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        stage = "pce_execution"
        result = PolyBenchPCERunner(
            config,
            capacity,
            checkpoint_dir=checkpoint_dir,
            checkpoint_identity=checkpoint_identity(
                case,
                execution_fingerprint=str(manifest["fingerprint"]),
            ),
            attempt_dir=attempt_dir,
        ).run(case)
        stage = "output_write"
        atomic_json(
            output_path,
            {
                "schema_version": 1,
                "status": "completed",
                "mode": "polybench_pce",
                "fingerprint": manifest["fingerprint"],
                "task_index": manifest["task_index"],
                "instance_id": case.instance_id,
                "row_sha256": case.row_sha256,
                "attempt": attempt,
                "attempt_evidence_dir": str(attempt_dir),
                "checkpoint_dir": str(checkpoint_dir),
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                **result,
            },
        )
        return 0
    except Exception as exc:
        phase = getattr(exc, "phase", stage)
        reason = getattr(exc, "reason", type(exc).__name__)
        failure = {
            "schema_version": 1,
            # Match the existing Online HPC worker boundary: an individual
            # worker exception is retryable transport output. Identity/schema
            # violations are still blocked by the host-side output validator.
            "status": "retryable_failed",
            "mode": "polybench_pce",
            "fingerprint": manifest.get("fingerprint"),
            "task_index": manifest.get("task_index"),
            "instance_id": case.instance_id if case else manifest.get("instance_id"),
            "attempt": attempt,
            "attempt_evidence_dir": str(attempt_dir),
            "checkpoint_dir": str(checkpoint_dir),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_stage": stage,
            "failure_category": _category(exc),
            "terminal_phase": str(phase),
            "terminal_reason": str(reason),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "final_validation_label": None,
        }
        trajectory = getattr(exc, "trajectory", None)
        if trajectory is not None:
            failure["failure_trajectory"] = list(trajectory)
        evaluator_evidence = getattr(exc, "evidence", None)
        if isinstance(evaluator_evidence, dict):
            failure["evaluator_evidence"] = evaluator_evidence
        atomic_json(attempt_dir / "failure.json", failure)
        atomic_json(output_path, failure)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--checkpoint-dir", required=True, type=Path)
    parser.add_argument("--attempt", required=True, type=int)
    args = parser.parse_args()
    return run_task(
        config_path=args.config,
        task_manifest_path=args.task_manifest,
        output_path=args.output,
        attempt_dir=args.attempt_dir,
        checkpoint_dir=args.checkpoint_dir,
        attempt=args.attempt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
