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
from src.optimization.audit import JsonlLogger
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


def _retry_disposition(exc: BaseException, *, stage: str = "") -> str:
    explicit = getattr(exc, "retry_disposition", None)
    if explicit:
        return str(explicit)
    # UnicodeDecodeError is a ValueError subclass, but invalid bytes emitted
    # by a repository command are an execution failure, not evidence that the
    # frozen input or task identity is invalid.
    if isinstance(exc, UnicodeError):
        return "retry_same_phase"
    # Only malformed shared inputs/configuration block the whole run. A
    # case-local runtime failure (including repository preparation) is retried
    # and, after exhaustion, consolidated as operationally incomplete by the
    # host. One difficult repository must not suppress the other array cases.
    if isinstance(exc, (FatalError, ValueError)) and stage in {
        "input_load",
        "config_load",
    }:
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
    dataset=None,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    case: SWEVerifiedPCECase | None = None
    config = None
    stage = "input_load"
    mode = "swe_verified_pce"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        operation = manifest.get("operation", "pce")
        if operation not in {"pce", "prepare_workspace"}:
            raise ValueError("unsupported repository PCE Worker operation")
        mode = manifest.get("mode", mode)
        if dataset is None and mode != "swe_verified_pce":
            from src.pce_dataset import repository_pce_dataset
            dataset = repository_pce_dataset(mode)
        if dataset is not None and dataset.mode != mode:
            raise ValueError("PCE Worker dataset identity mismatch")
        case_loader = dataset.case_from_dict if dataset else SWEVerifiedPCECase.from_dict
        config_loader = dataset.load_config if dataset else load_swe_verified_pce_config
        runner_class = dataset.runner_class if dataset else SWEVerifiedPCERunner
        case = case_loader(dict(manifest["case"]))
        stage = "config_load"
        config = config_loader(config_path, **({"require_api_keys": False}
                                               if operation == "prepare_workspace" else {}))
        stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        stage = "pce_execution"
        if operation == "prepare_workspace":
            stage = "input_load"
            if config.workspace_preparation != "separate_slurm_v1":
                raise ValueError("workspace preparation Worker requires explicit configuration")
            parent = config.run_dir / "hpc_tasks" / "pce" / manifest["pce_fingerprint"]
            parent_manifest = json.loads((parent / "tasks" / f"task_{manifest['pce_task_index']:04d}.json").read_text())
            if parent_manifest["case"] != manifest["case"]:
                raise ValueError("workspace preparation case differs from its PCE authority")
            checkpoint_dir = parent / "checkpoints" / f"task_{manifest['pce_task_index']:04d}"
            stage = "workspace_preparation"
            runner = runner_class(config, capacity, checkpoint_dir=checkpoint_dir,
                checkpoint_identity=checkpoint_identity(case, execution_fingerprint=manifest["pce_fingerprint"]),
                attempt_dir=attempt_dir)
            result = runner.prepare_workspace(case, manifest["prepared_phase"])
            atomic_json(output_path, {
                "schema_version": 1, "status": "completed", "mode": mode,
                **{name: manifest[name] for name in ("fingerprint", "task_index", "instance_id",
                     "pce_fingerprint", "pce_task_index", "pce_attempt", "prepared_phase")},
                "attempt": attempt, "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(), **result,
            })
            return 0
        runner = runner_class(
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
        attempt_relative_path = attempt_dir.relative_to(config.run_dir).as_posix()
        checkpoint_relative_path = checkpoint_dir.relative_to(
            config.run_dir
        ).as_posix()
        atomic_json(
            output_path,
            {
                "schema_version": 1,
                "status": "completed",
                "mode": mode,
                "fingerprint": manifest["fingerprint"],
                "task_index": manifest["task_index"],
                "instance_id": case.instance_id,
                "pce_run_index": manifest.get("pce_run_index", 1),
                "row_sha256": case.row_sha256,
                "attempt": attempt,
                "attempt_evidence_relative_path": attempt_relative_path,
                "checkpoint_relative_path": checkpoint_relative_path,
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                **result,
            },
        )
        return 0
    except Exception as exc:
        phase = getattr(exc, "phase", stage)
        reason = getattr(exc, "reason", type(exc).__name__)
        retry_disposition = _retry_disposition(exc, stage=stage)
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
            "mode": mode,
            "fingerprint": manifest.get("fingerprint"),
            "task_index": manifest.get("task_index"),
            "instance_id": case.instance_id if case else manifest.get("instance_id"),
            "pce_run_index": manifest.get("pce_run_index", 1),
            "attempt": attempt,
            "attempt_evidence_relative_path": (
                attempt_dir.relative_to(config.run_dir).as_posix()
                if config is not None
                else None
            ),
            "checkpoint_relative_path": (
                checkpoint_dir.relative_to(config.run_dir).as_posix()
                if config is not None
                else None
            ),
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
            "errno": getattr(exc, "errno", None),
            "error": str(exc),
            "final_validation_label": None,
        }
        trajectory = getattr(exc, "trajectory", None)
        if trajectory is not None:
            failure["failure_trajectory"] = list(trajectory)
        evaluator_evidence = getattr(exc, "evidence", None)
        if isinstance(evaluator_evidence, dict):
            failure["evaluator_evidence"] = evaluator_evidence
        atomic_json(output_path, failure)
        JsonlLogger(attempt_dir / "audit_events.jsonl").write_optional(
            "worker_failed", failure_stage=stage, error_type=type(exc).__name__)
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
