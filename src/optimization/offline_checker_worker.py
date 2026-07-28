"""Run one Offline Checker Agent as one Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import FatalError
from src.optimization.audit import text_sha256
from src.optimization.checker import (
    CheckerOutputContractError,
    DockerChecker,
    checker_retry_feedback,
)
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.models import GEPACase, RepositoryRef


def _load_retry_feedback(previous_output_path: Path | None) -> str:
    if previous_output_path is None or not previous_output_path.is_file():
        return ""
    try:
        previous_output = json.loads(
            previous_output_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return ""
    if (
        previous_output.get("status") == "agent_failed"
        and previous_output.get("failure_kind") == "checker_output_contract"
        and isinstance(previous_output.get("error"), str)
    ):
        return checker_retry_feedback(previous_output["error"])
    return ""


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, CheckerOutputContractError):
        return "output_contract"
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
    rules = ""
    retry_feedback = ""
    failure_stage = "input_load"
    try:
        manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
        rules = Path(str(manifest["rules_path"])).read_text(encoding="utf-8")
        payload = dict(manifest["checker_payload"])
        repository = dict(payload["repository"])
        case = GEPACase(
            instance_id=str(manifest["instance_id"]),
            split=str(manifest["split"]),
            # Labels and execution-after evidence are deliberately absent from
            # the worker manifest. This placeholder is never shown to the
            # Checker and is not used to produce its prediction.
            resolved=False,
            issue_description=str(payload["issue_description"]),
            plan=str(payload["plan"]),
            repository=RepositoryRef(
                repo=str(repository["repo"]),
                base_commit=str(repository["base_commit"]),
                instance_id=str(repository["instance_id"]),
            ),
            asi={},
        )
        failure_stage = "retry_context"
        retry_feedback = _load_retry_feedback(previous_output_path)
        if retry_feedback:
            atomic_json(
                attempt_dir / "retry_feedback.json",
                {
                    "schema_version": 1,
                    "source": str(previous_output_path),
                    "feedback": retry_feedback,
                },
            )
        failure_stage = "config_load"
        config = load_optimization_config(config_path)
        config = replace(
            config,
            run_dir=attempt_dir,
            checker=replace(config.checker, max_attempts=1),
        )
        failure_stage = "runtime_setup"
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=False,
        )
        failure_stage = "checker_execution"
        output = DockerChecker(config, capacity)(
            case,
            rules,
            retry_feedback=retry_feedback,
        )
        failure_stage = "output_write"
        atomic_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "mode": "offline_checker",
                "fingerprint": manifest["fingerprint"],
                "instance_id": case.instance_id,
                "split": case.split,
                "candidate_sha256": text_sha256(rules),
                "retry_feedback": retry_feedback,
                "checker_output": output.to_dict(include_trajectory=True),
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
            "mode": "offline_checker",
            "fingerprint": manifest.get("fingerprint"),
            "instance_id": manifest.get("instance_id"),
            "candidate_sha256": text_sha256(rules),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failure_stage": failure_stage,
            "failure_category": _failure_category(exc),
            "failure_kind": (
                "checker_output_contract"
                if isinstance(exc, CheckerOutputContractError)
                else "operational"
            ),
            "retry_feedback": retry_feedback,
        }
        trajectory = getattr(exc, "checker_trajectory", ())
        if trajectory:
            atomic_json(
                attempt_dir / "checker_trajectory.json",
                {
                    "schema_version": 1,
                    "messages": list(trajectory),
                },
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
