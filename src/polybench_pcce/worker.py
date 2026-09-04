"""Run one PCCE PC review or CE execution as one Slurm task."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import AgentTaskError, FatalError
from src.optimization.checker import CheckerAgentTimeout, CheckerOutputContractError
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pcce.config import load_polybench_pcce_config
from src.polybench_pcce.models import CEAssignment, PCCECase, PCReviewAssignment
from src.polybench_pcce.runner import PolyBenchPCCERunner
from src.polybench_pce.evaluator import PolyBenchEvaluatorOperationalError
from src.polybench_pce.models import PolyBenchPCECase


def _retry_disposition(exc: BaseException) -> str:
    explicit = getattr(exc, "retry_disposition", None)
    if explicit:
        return str(explicit)
    if isinstance(exc, UnicodeError):
        return "retry_same_phase"
    if isinstance(exc, (FatalError, ValueError, MemoryError)):
        return "block_run"
    if isinstance(
        exc, (AgentTaskError, CheckerAgentTimeout, CheckerOutputContractError)
    ):
        return "retry_fresh_agent"
    return "retry_same_phase"


def _case(value: dict[str, Any]) -> PCCECase:
    return PCCECase(
        source=PolyBenchPCECase.from_dict(dict(value["source"])),
        baseline_plan=str(value["baseline_plan"]),
        # Checker-only PC manifests intentionally omit post-implementation
        # outcome fields. Full PCCE and CE manifests retain them unchanged.
        baseline_resolved=bool(value.get("baseline_resolved", False)),
        baseline_outcome_sha256=str(value.get("baseline_outcome_sha256", "")),
    )


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
    checkpoint_dir: Path,
    attempt: int,
) -> int:
    started = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        config = load_polybench_pcce_config(config_path)
        phase = str(manifest["phase"])
        case = _case(dict(manifest["case"]))
        capacity = configure_docker_capacity(
            config.pce.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        runner = PolyBenchPCCERunner(
            config,
            capacity,
            checkpoint_dir=checkpoint_dir,
            attempt_dir=attempt_dir,
        )
        stage = f"{phase}_execution"
        if phase == "pc":
            guideline = (config.run_dir / str(manifest["guideline_relpath"])).read_text(
                encoding="utf-8"
            )
            if text_sha256(guideline) != manifest.get("guideline_sha256"):
                raise FatalError("PCCE guideline artifact hash mismatch")
            result = runner.run_pc(
                PCReviewAssignment(
                    case=case,
                    review_index=int(manifest["review_index"]),
                    rejection_count=int(manifest["rejection_count"]),
                    input_plan=str(manifest["input_plan"]),
                    previous_feedback=str(manifest.get("previous_feedback", "")),
                ),
                fingerprint=str(manifest["fingerprint"]),
                guideline=guideline,
            )
        elif phase == "ce":
            accepted_path = config.run_dir / str(manifest["accepted_review_relpath"])
            accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
            checker_output = accepted.get("checker_output")
            accepted_plan = manifest.get("accepted_plan")
            if accepted.get("status") != "completed":
                raise FatalError("PCCE CE source review is not completed")
            if accepted.get("instance_id") != case.instance_id:
                raise FatalError("PCCE CE source review instance mismatch")
            if (
                not isinstance(checker_output, dict)
                or checker_output.get("should_proceed") is not True
            ):
                raise FatalError("PCCE CE source review did not approve the plan")
            if not isinstance(accepted_plan, str) or not accepted_plan.strip():
                raise FatalError("PCCE CE manifest lacks the accepted plan")
            if accepted.get("plan") != accepted_plan:
                raise FatalError("PCCE CE source review plan mismatch")
            if text_sha256(accepted_plan) != manifest.get("accepted_plan_sha256"):
                raise FatalError("PCCE CE accepted plan hash mismatch")
            result = runner.run_ce(
                CEAssignment(case, accepted_path, accepted_plan),
                fingerprint=str(manifest["fingerprint"]),
            )
        else:
            raise ValueError(f"unknown PCCE phase: {phase}")
        atomic_json(
            output_path,
            {
                "schema_version": 1,
                "status": "completed",
                "mode": "polybench_pcce",
                "phase": phase,
                "fingerprint": manifest["fingerprint"],
                "task_index": manifest["task_index"],
                "instance_id": case.instance_id,
                "attempt": attempt,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                **result,
            },
        )
        return 0
    except Exception as exc:
        disposition = _retry_disposition(exc)
        failure = {
            "schema_version": 1,
            "status": "blocking_failed"
            if disposition == "block_run"
            else "retryable_failed",
            "mode": "polybench_pcce",
            "phase": manifest.get("phase"),
            "fingerprint": manifest.get("fingerprint"),
            "task_index": manifest.get("task_index"),
            "instance_id": manifest.get("instance_id"),
            "attempt": attempt,
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_stage": stage,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "retry_disposition": disposition,
        }
        trajectory = getattr(exc, "trajectory", None) or getattr(
            exc, "checker_trajectory", None
        )
        if trajectory is not None:
            failure["failure_trajectory"] = list(trajectory)
        evidence = getattr(exc, "evidence", None)
        if isinstance(exc, PolyBenchEvaluatorOperationalError) and isinstance(
            evidence, dict
        ):
            failure["evaluator_evidence"] = evidence
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
