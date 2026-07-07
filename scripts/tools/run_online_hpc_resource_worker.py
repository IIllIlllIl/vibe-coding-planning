#!/usr/bin/env python3
"""Run one online GEPA rollout worker for an HPC resource pilot.

This script is intended to run inside a Slurm job created by ``ulhpc-submit``.
It deliberately avoids calling ``sbatch`` itself, so module loading, rsync,
dataset staging, persistent output symlinks, and log capture stay owned by the
shared ULHPC submission tool.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tools.prepare_online_hpc_resource_pilot import _select_cases  # noqa: E402
from src.optimization.online_config import load_online_optimization_config  # noqa: E402
from src.optimization.online_dataset import load_online_snapshot  # noqa: E402
from src.optimization.online_hpc_executor import OnlineRolloutBatchStore  # noqa: E402
from src.optimization.online_rollout_worker import run_task  # noqa: E402


def prepare_worker_task(
    *,
    config_path: Path,
    split: str,
    instance_ids: Sequence[str],
    limit: int,
    task_index: int,
) -> dict[str, object]:
    """Create one task manifest and return worker paths.

    The manifest contains only deploy-time inputs: issue text, repository
    identity/base commit, and candidate rules path. Resolved labels,
    trajectories, patches, and evaluator output are produced only after the
    worker runs.
    """

    config = load_online_optimization_config(config_path, require_api_keys=False)
    train, validation = load_online_snapshot(config.dataset_snapshot)
    configured_ids = (
        config.dataset.train_instance_ids
        if split == "train"
        else config.dataset.validation_instance_ids
    )
    cases = _select_cases(
        train=train,
        validation=validation,
        split=split,
        instance_ids=instance_ids or configured_ids,
        limit=limit,
    )
    if task_index < 0 or task_index >= len(cases):
        raise ValueError(
            f"--task-index must be in [0, {len(cases) - 1}], got {task_index}"
        )

    rules = config.initial_rules_path.read_text(encoding="utf-8")
    batch_dir, tasks = OnlineRolloutBatchStore(config.run_dir).create(
        batch=[cases[task_index]],
        rules=rules,
        split=split,
        capture_traces=True,
    )
    task = tasks[0]
    return {
        "batch_dir": str(batch_dir),
        "task_manifest": str(task.manifest_path),
        "output": str(task.output_path),
        "worker_run_dir": str(task.worker_run_dir),
        "instance_id": task.case.instance_id,
        "split": task.case.split,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one online GEPA resource-pilot rollout worker.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/gepa_online_planning_hpc_resource_pilot_20260706.yaml"),
    )
    parser.add_argument("--split", choices=("train", "validation"), default="train")
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--task-index", type=int, default=0)
    args = parser.parse_args()

    task_info = prepare_worker_task(
        config_path=args.config,
        split=args.split,
        instance_ids=args.instance_id,
        limit=args.limit,
        task_index=args.task_index,
    )
    rc = run_task(
        config_path=args.config,
        task_manifest_path=Path(str(task_info["task_manifest"])),
        output_path=Path(str(task_info["output"])),
        worker_run_dir=Path(str(task_info["worker_run_dir"])),
    )
    print(
        json.dumps(
            {
                "event": "online_hpc_resource_worker_finished",
                "returncode": rc,
                **task_info,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
