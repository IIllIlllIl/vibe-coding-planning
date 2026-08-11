"""Run one dataset-aware check-only Checker as one fresh Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import FatalError
from src.offline_check_only.config import load_check_only_config
from src.offline_check_only.dataset import CheckOnlyCase
from src.optimization.audit import text_sha256
from src.optimization.checker import (
    CheckerAgentTimeout,
    CheckerOutputContractError,
    DockerChecker,
)
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.offline_checker_worker import (
    _failure_category,
    _load_retry_feedback,
)


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
    previous_output_path: Path | None = None,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    guideline = ""
    retry_feedback = ""
    failure_stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        guideline = Path(str(manifest["rules_path"])).read_text(encoding="utf-8")
        payload = dict(manifest["checker_payload"])
        repository = {key: str(value) for key, value in dict(payload["repository"]).items()}
        case = CheckOnlyCase(
            instance_id=str(manifest["benchmark_instance_id"]),
            split=str(manifest["split"]),
            resolved=False,
            issue_description=str(payload["issue_description"]),
            plan=str(payload["plan"]),
            repository=repository,
            task_category="",
            language="",
        )
        failure_stage = "retry_context"
        retry_feedback = _load_retry_feedback(previous_output_path)
        if retry_feedback:
            atomic_json(
                attempt_dir / "retry_feedback.json",
                {"schema_version": 1, "source": str(previous_output_path), "feedback": retry_feedback},
            )
        failure_stage = "config_load"
        check_only = load_check_only_config(config_path)
        config = replace(
            check_only.runtime,
            run_dir=attempt_dir,
            checker=replace(check_only.runtime.checker, max_attempts=1),
        )
        failure_stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        failure_stage = "checker_execution"
        output = DockerChecker(config, capacity)(
            case,  # type: ignore[arg-type]
            guideline,
            retry_feedback=retry_feedback,
            trajectory_journal_path=attempt_dir / "checker_trajectory.jsonl",
        )
        failure_stage = "output_write"
        atomic_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "mode": "offline_check_only",
                "fingerprint": manifest["fingerprint"],
                "instance_id": manifest["instance_id"],
                "benchmark_instance_id": case.instance_id,
                "guideline_label": manifest["guideline_label"],
                "candidate_sha256": text_sha256(guideline),
                "retry_feedback": retry_feedback,
                "checker_output": output.to_dict(include_trajectory=True),
            },
        )
        return 0
    except (CheckerAgentTimeout, Exception) as exc:
        failure = {
            "status": "blocking_failed" if isinstance(exc, (FatalError, MemoryError)) else "agent_failed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": "offline_check_only",
            "fingerprint": manifest.get("fingerprint"),
            "instance_id": manifest.get("instance_id"),
            "benchmark_instance_id": manifest.get("benchmark_instance_id"),
            "guideline_label": manifest.get("guideline_label"),
            "candidate_sha256": text_sha256(guideline),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failure_stage": failure_stage,
            "failure_category": _failure_category(exc),
            "failure_kind": (
                "checker_agent_timeout"
                if isinstance(exc, CheckerAgentTimeout)
                else "checker_output_contract"
                if isinstance(exc, CheckerOutputContractError)
                else "operational"
            ),
            "retry_feedback": retry_feedback,
        }
        trajectory = getattr(exc, "checker_trajectory", None)
        if trajectory is not None:
            atomic_json(
                attempt_dir / "checker_trajectory.json",
                {"schema_version": 1, "messages": list(trajectory)},
            )
        atomic_json(attempt_dir / "failure.json", failure)
        atomic_json(output_path, failure)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    parser.add_argument("--previous-output", type=Path)
    args = parser.parse_args()
    return run_task(
        config_path=args.config,
        task_manifest_path=args.task_manifest,
        output_path=args.output,
        attempt_dir=args.attempt_dir,
        previous_output_path=args.previous_output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
