"""Run one frozen SWE-Verified PCE instance as one Slurm array element."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import AgentTaskError, FatalError
from src.optimization.hpc.task_batch import atomic_json
from src.swe_verified_pce.config import load_swe_verified_pce_config
from src.swe_verified_pce.evaluator import SWEVerifiedEvaluatorOperationalError
from src.swe_verified_pce.models import SWEVerifiedPCECase
from src.swe_verified_pce.runner import SWEVerifiedPCERunner, checkpoint_identity


def _category(exc: BaseException) -> str:
    if isinstance(exc, AgentTaskError):
        return "agent"
    if isinstance(exc, SWEVerifiedEvaluatorOperationalError):
        return "evaluator"
    if isinstance(exc, MemoryError):
        return "memory"
    if isinstance(exc, FatalError):
        return "fatal"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "io"
    if isinstance(exc, UnicodeError):
        return "encoding"
    if isinstance(exc, ValueError):
        return "validation"
    return "unexpected"


def _retry_disposition(exc: BaseException) -> str:
    explicit = getattr(exc, "retry_disposition", None)
    if explicit:
        return str(explicit)
    # UnicodeDecodeError is a ValueError subclass, but invalid bytes emitted
    # by a repository command are an execution failure, not evidence that the
    # frozen input or task identity is invalid.
    if isinstance(exc, UnicodeError):
        return "retry_same_phase"
    if isinstance(exc, (FatalError, ValueError)):
        return "block_run"
    if isinstance(exc, AgentTaskError):
        return "retry_fresh_agent"
    return "retry_same_phase"


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
    case: SWEVerifiedPCECase | None = None
    stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        case = SWEVerifiedPCECase.from_dict(dict(manifest["case"]))
        stage = "config_load"
        config = load_swe_verified_pce_config(config_path)
        stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        stage = "pce_execution"
        runner = SWEVerifiedPCERunner(
            config,
            capacity,
            checkpoint_dir=checkpoint_dir,
            checkpoint_identity=checkpoint_identity(
                case,
                execution_fingerprint=str(manifest["fingerprint"]),
            ),
            attempt_dir=attempt_dir,
        )
        result = runner.run(case)
        stage = "output_write"
        atomic_json(
            output_path,
            {
                "schema_version": 1,
                "status": "completed",
                "mode": "swe_verified_pce",
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
        retry_disposition = _retry_disposition(exc)
        outcome_reason = str(getattr(exc, "outcome_reason", reason))
        failure = {
            "schema_version": 1,
            # Match the existing Online HPC worker boundary: an individual
            # worker exception is retryable transport output. Identity/schema
            # violations are still blocked by the host-side output validator.
            "status": (
                "blocking_failed"
                if retry_disposition == "block_run"
                else "retryable_failed"
            ),
            "mode": "swe_verified_pce",
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
            "task_outcome": "unknown",
            "outcome_reason": outcome_reason,
            "retry_disposition": retry_disposition,
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
