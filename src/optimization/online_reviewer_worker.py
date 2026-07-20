"""Run one repo-grounded Online GEPA Reviewer Slurm task."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.data.instance_loader import InstanceLoader
from src.environment.docker_env import configure_docker_capacity
from src.evaluator.swe_evaluator import derive_image_name
from src.exceptions import FatalError
from src.optimization.audit import text_sha256
from src.optimization.online_config import load_online_optimization_config
from src.optimization.online_hpc_executor import OnlineRolloutBatchStore
from src.optimization.online_reflection_reviewer import OnlineInstanceReflectionReviewer
from src.optimization.online_rollout_worker import case_from_manifest


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


def run_task(
    *,
    config_path: Path,
    task_manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
) -> int:
    started_at = _now()
    manifest = json.loads(task_manifest_path.read_text(encoding="utf-8"))
    rollout_manifest = json.loads(
        Path(manifest["rollout_manifest_path"]).read_text(encoding="utf-8")
    )
    case = case_from_manifest(rollout_manifest)
    rules = Path(rollout_manifest["rules_path"]).read_text(encoding="utf-8")
    try:
        config = load_online_optimization_config(config_path)
        config = replace(config, run_dir=attempt_dir)
        rollout = OnlineRolloutBatchStore.load_output(
            Path(manifest["rollout_output_path"]),
            expected_instance_id=case.instance_id,
            expected_candidate_sha256=text_sha256(rules),
        )
        loader = InstanceLoader(
            dataset=config.dataset.dataset,
            dataset_type=config.dataset.dataset_type,
            language_filter=config.dataset.language_filter,
        )
        instance_info = loader.load_instance(case.instance_id)
        instance_info.setdefault("instance_id", case.instance_id)
        image_name = derive_image_name(instance_info)
        dataset_type = instance_info.get("dataset_type", "")
        is_pro = dataset_type == "pro" or "dockerhub_tag" in instance_info
        workdir = "/app" if is_pro else config.docker.workdir
        capacity = configure_docker_capacity(
            config.docker,
            max_concurrent=1,
            enable_docker_maintenance=config.container.runtime == "docker",
        )
        review, trajectory = OnlineInstanceReflectionReviewer(
            config,
            capacity,
        ).review(
            case=case,
            rules=rules,
            image_name=image_name,
            workdir=workdir,
            evidence={
                "generated_plan": rollout.plan,
                "plan_trajectory": list(rollout.plan_trajectory),
                "code_trajectory": list(rollout.code_trajectory),
                "generated_patch": rollout.patch,
                "evaluator_result": rollout.evaluator_result,
                "rollout_summary": {
                    "resolved": rollout.resolved,
                    "score": float(rollout.resolved),
                    "terminal_phase": rollout.terminal_phase,
                    "terminal_reason": rollout.terminal_reason,
                },
            },
            phase_root=attempt_dir / "review",
        )
        _write_json(
            output_path,
            {
                "status": "completed",
                "started_at": started_at,
                "finished_at": _now(),
                "instance_id": case.instance_id,
                "candidate_sha256": text_sha256(rules),
                "review": review,
                "trajectory": trajectory,
            },
        )
        return 0
    except Exception as exc:
        blocking = isinstance(exc, (FatalError, MemoryError))
        _write_json(
            attempt_dir / "failure.json",
            {
                "status": "blocking_failed" if blocking else "agent_failed",
                "started_at": started_at,
                "finished_at": _now(),
                "instance_id": str(manifest.get("instance_id", "")),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        _write_json(
            output_path,
            json.loads((attempt_dir / "failure.json").read_text(encoding="utf-8")),
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    args = parser.parse_args()
    return run_task(
        config_path=args.config,
        task_manifest_path=args.task_manifest,
        output_path=args.output,
        attempt_dir=args.attempt_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
