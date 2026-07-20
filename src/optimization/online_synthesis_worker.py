"""Run one Online GEPA Synthesis Agent Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import FatalError
from src.optimization.online_config import load_online_optimization_config
from src.optimization.online_reflection import OnlinePlanningReflectionProposer


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_task(
    *,
    config_path: Path,
    manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        config = load_online_optimization_config(config_path)
        config = replace(
            config,
            run_dir=attempt_dir,
            execution=replace(config.execution, backend="local_apptainer"),
        )
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=config.container.runtime == "docker",
        )
        proposal = OnlinePlanningReflectionProposer(config, capacity)(
            dict(manifest["candidate"]),
            dict(manifest["reflective_dataset"]),
            list(manifest["components_to_update"]),
        )
        _write_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "proposal_fingerprint": manifest["proposal_fingerprint"],
                "proposal": proposal,
            },
        )
        return 0
    except Exception as exc:
        blocking = isinstance(exc, (FatalError, MemoryError))
        failure = {
            "status": "blocking_failed" if blocking else "agent_failed",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "proposal_fingerprint": manifest.get("proposal_fingerprint"),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        _write_json(attempt_dir / "failure.json", failure)
        _write_json(output_path, failure)
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
