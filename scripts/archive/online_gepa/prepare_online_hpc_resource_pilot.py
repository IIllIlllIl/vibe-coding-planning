"""Prepare or submit a small online GEPA HPC resource-measurement pilot."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.optimization.online_config import load_online_optimization_config  # noqa: E402
from src.optimization.online_dataset import load_online_snapshot  # noqa: E402
from src.optimization.online_hpc_executor import (  # noqa: E402
    OnlineRolloutBatchStore,
    build_slurm_array_script,
)
from src.optimization.online_models import OnlineGEPACase  # noqa: E402


SACCT_FORMAT = "JobID,JobName,State,Elapsed,AllocCPUS,TotalCPU,ReqMem,MaxRSS"


def _select_cases(
    *,
    train: Sequence[OnlineGEPACase],
    validation: Sequence[OnlineGEPACase],
    split: str,
    instance_ids: Sequence[str],
    limit: int,
) -> list[OnlineGEPACase]:
    source = list(train if split == "train" else validation)
    if instance_ids:
        by_id = {case.instance_id: case for case in source}
        missing = set(instance_ids) - set(by_id)
        if missing:
            raise ValueError(f"instance IDs not found in {split}: {sorted(missing)}")
        source = [by_id[instance_id] for instance_id in instance_ids]
    if limit < 1:
        raise ValueError("--limit must be positive")
    selected = source[:limit]
    if not selected:
        raise ValueError(f"no {split} cases selected")
    return selected


def prepare_pilot(
    *,
    config_path: Path,
    split: str,
    instance_ids: Sequence[str],
    limit: int,
    submit: bool,
) -> dict[str, object]:
    config = load_online_optimization_config(config_path, require_api_keys=False)
    if config.execution.backend != "hpc_slurm":
        config = replace(
            config,
            execution=replace(config.execution, backend="hpc_slurm"),
        )
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
    rules = config.initial_rules_path.read_text(encoding="utf-8")
    store = OnlineRolloutBatchStore(config.run_dir)
    batch_dir, tasks = store.create(
        batch=cases,
        rules=rules,
        split=split,
        capture_traces=True,
    )
    script = build_slurm_array_script(
        config_path=config.hpc.worker_config_path,
        batch_dir=str(batch_dir),
        task_count=len(tasks),
        job_name=f"{config.hpc.job_name_prefix}-resource-{batch_dir.name}",
        partition=config.hpc.partition,
        cpus_per_task=config.hpc.cpus_per_task,
        mem=config.hpc.mem,
        time_limit=config.hpc.time,
        max_running_array_tasks=min(
            config.hpc.max_running_array_tasks,
            len(tasks),
        ),
        remote_env_file=config.hpc.remote_env_file,
        python_module=config.hpc.python_module,
        container_module=config.hpc.container_module,
        python_bin=config.hpc.python_bin,
    )
    script_path = batch_dir / "resource_pilot_array.sbatch"
    script_path.write_text(script, encoding="utf-8")
    manifest = {
        "mode": "online_hpc_resource_pilot",
        "config_path": str(config_path),
        "batch_dir": str(batch_dir),
        "script_path": str(script_path),
        "split": split,
        "instances": [case.instance_id for case in cases],
        "task_count": len(tasks),
        "cpus_per_task": config.hpc.cpus_per_task,
        "mem": config.hpc.mem,
        "time": config.hpc.time,
        "max_running_array_tasks": min(
            config.hpc.max_running_array_tasks,
            len(tasks),
        ),
        "resource_measurement": {
            "sacct_format": SACCT_FORMAT,
            "cpu_utilization": "TotalCPU / (Elapsed * AllocCPUS)",
            "memory_utilization": "MaxRSS / ReqMem",
        },
        "submitted": False,
    }
    if submit:
        result = subprocess.run(
            ["sbatch", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        manifest["sbatch_returncode"] = result.returncode
        manifest["sbatch_stdout"] = result.stdout.strip()
        manifest["sbatch_stderr"] = result.stderr.strip()
        if result.returncode != 0:
            raise RuntimeError(
                "sbatch failed: " + (result.stderr or result.stdout).strip()[:1000]
            )
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        if match:
            manifest["job_id"] = match.group(1)
            manifest["sacct_command"] = (
                f"sacct -j {match.group(1)} --format={SACCT_FORMAT}"
            )
        manifest["submitted"] = True
    manifest_path = batch_dir / "resource_pilot_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or submit an online GEPA Slurm resource pilot.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/archive/online_tests/gepa_online_planning_hpc_resource_pilot_20260706.yaml"),
    )
    parser.add_argument("--split", choices=("train", "validation"), default="train")
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()

    manifest = prepare_pilot(
        config_path=args.config,
        split=args.split,
        instance_ids=args.instance_id,
        limit=args.limit,
        submit=args.submit,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
