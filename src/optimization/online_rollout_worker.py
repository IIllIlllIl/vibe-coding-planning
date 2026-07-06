"""CLI worker for one online GEPA Plan-Code-Test rollout.

The worker is intentionally stateless: a controller writes a task manifest,
Slurm runs this module for one task, and the worker writes exactly one result
JSON file. GEPA state remains owned by the controller.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.environment.docker_env import configure_docker_capacity
from src.optimization.audit import text_sha256
from src.optimization.models import RepositoryRef
from src.optimization.online_config import load_online_optimization_config
from src.optimization.online_models import OnlineGEPACase, OnlineRolloutOutput
from src.optimization.online_rollout import OnlinePCTRolloutRunner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def case_from_manifest(manifest: dict[str, Any]) -> OnlineGEPACase:
    repository = manifest["repository"]
    return OnlineGEPACase(
        instance_id=str(manifest["instance_id"]),
        split=str(manifest.get("split", "train")),
        issue_description=str(manifest["issue_description"]),
        repository=RepositoryRef(
            repo=str(repository["repo"]),
            base_commit=str(repository["base_commit"]),
            instance_id=str(repository["instance_id"]),
        ),
    )


def output_to_json(output: OnlineRolloutOutput) -> dict[str, Any]:
    return {
        "resolved": output.resolved,
        "score": float(output.resolved),
        "plan": output.plan,
        "patch": output.patch,
        "plan_trajectory": list(output.plan_trajectory),
        "code_trajectory": list(output.code_trajectory),
        "evaluator_result": output.evaluator_result,
        "attribution_hint": output.attribution_hint,
    }


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    worker_run_dir: Path | None = None,
) -> int:
    started_at = _now()
    manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
    rules_path = Path(str(manifest["rules_path"]))
    rules = rules_path.read_text(encoding="utf-8")
    case = case_from_manifest(manifest)

    try:
        config = load_online_optimization_config(config_path)
        if worker_run_dir is not None:
            config = replace(config, run_dir=worker_run_dir)
        capacity = configure_docker_capacity(config.docker, max_concurrent=1)
        output = OnlinePCTRolloutRunner(config, capacity)(case, rules)
        _write_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "mode": "online_planning",
                "instance_id": case.instance_id,
                "split": case.split,
                "candidate_sha256": text_sha256(rules),
                **output_to_json(output),
            },
        )
        return 0
    except Exception as exc:
        _write_json(
            output_path,
            {
                "status": "failed",
                "started_at": started_at,
                "finished_at": _now(),
                "mode": "online_planning",
                "instance_id": str(manifest.get("instance_id", "")),
                "split": str(manifest.get("split", "")),
                "candidate_sha256": text_sha256(rules),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "retryable": True,
            },
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one online GEPA rollout task and write JSON output.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker-run-dir", type=Path)
    args = parser.parse_args()

    return run_task(
        config_path=args.config,
        task_manifest_path=args.task_manifest,
        output_path=args.output,
        worker_run_dir=args.worker_run_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
