"""Run one no-container Behavioral Reflection Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from src.optimization.behavioral_runtime import BehavioralLocalReflectionProposer
from src.optimization.config import OfflineExecutionConfig, load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


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
        if manifest.get("mode") != "behavioral_reflection":
            raise ValueError("worker manifest mode is not behavioral_reflection")
        failure_stage = "config_load"
        config = load_optimization_config(config_path)
        config = replace(
            config,
            run_dir=attempt_dir,
            execution=OfflineExecutionConfig(backend="local"),
        )
        failure_stage = "reflection_execution"
        proposal = BehavioralLocalReflectionProposer(config)(
            dict(manifest["candidate"]),
            dict(manifest["reflective_dataset"]),
            list(manifest["components_to_update"]),
        )
        failure_stage = "output_write"
        atomic_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "mode": "behavioral_reflection",
                "fingerprint": manifest["fingerprint"],
                "outcome": "proposal",
                "proposal": proposal,
            },
        )
        return 0
    except Exception as exc:
        failure = {
            "status": "agent_failed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "mode": manifest.get("mode"),
            "fingerprint": manifest.get("fingerprint"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "failure_stage": failure_stage,
            "failure_kind": "operational",
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
