"""Prepare frozen PolyBench base-ancestor Git bundles before PCE Agents run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

from src.environment.apptainer_env import ApptainerEnvironment, ApptainerSifCache
from src.environment.docker_env import configure_docker_capacity
from src.environment.repository_history import RepositoryHistoryCache
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pce.config import load_polybench_pce_config
from src.polybench_pce.dataset import file_sha256, load_polybench_pce_cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    config = load_polybench_pce_config(args.config, require_api_keys=False)
    if config.plan_submission_protocol != "direct_human_markdown_v5":
        raise ValueError("history preheat requires the safe direct Plan protocol")
    if config.selection_manifest is None:
        raise ValueError("history preheat requires a frozen selection")
    cases, _, _ = load_polybench_pce_cases(
        config.dataset_snapshot, config.image_manifest
    )
    by_id = {case.instance_id: case for case in cases}
    requested = tuple(args.instance_id or config.instance_ids)
    if len(set(requested)) != len(requested):
        raise ValueError("history preheat instance IDs must be unique")
    if set(requested) - set(config.instance_ids):
        raise ValueError("history preheat IDs are outside frozen PCE selection")
    if set(requested) - set(by_id):
        raise ValueError("history preheat IDs are unavailable in the image manifest")
    if args.max_cases < 0:
        raise ValueError("--max-cases must be nonnegative")
    if args.max_cases:
        requested = requested[: args.max_cases]

    cache = RepositoryHistoryCache(
        config.container.sif_cache_dir.parent / "repository-history-cache-v1"
    )
    capacity = configure_docker_capacity(
        config.docker, max_concurrent=1, enable_docker_maintenance=False
    )
    workspace_root = config.run_dir / "history_preheat_workspaces"
    if not args.check_only:
        workspace_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str]] = []
    for instance_id in requested:
        case = by_id[instance_id]
        sif = Path(case.image.sif_path)
        expected_path = ApptainerSifCache(
            config.container.sif_cache_dir, capacity
        ).sif_path(case.image.requested_ref)
        if sif != expected_path or not sif.is_file():
            raise RuntimeError(f"frozen PolyBench SIF is missing: {instance_id}")
        if sif.stat().st_size != case.image.sif_bytes or file_sha256(sif) != case.image.sif_sha256:
            raise RuntimeError(f"frozen PolyBench SIF identity differs: {instance_id}")
        existing = cache.validate(
            sif_sha256=case.image.sif_sha256, base_commit=case.base_commit
        )
        if existing is not None:
            records.append({"instance_id": instance_id, "status": "cached"})
            continue
        if args.check_only:
            records.append({"instance_id": instance_id, "status": "missing"})
            continue
        with tempfile.TemporaryDirectory(
            prefix="polybench-history-", dir=workspace_root
        ) as temporary:
            workspace = Path(temporary) / "repository"
            env = ApptainerEnvironment(
                image=case.image.requested_ref,
                cwd=config.docker.workdir,
                sif_cache_dir=config.container.sif_cache_dir,
                capacity_window=capacity,
                timeout=config.plan.timeout,
                writable_tmpfs=config.container.writable_tmpfs,
                run_args=["--containall", "--no-mount", "cwd"],
                git_safe_directories=[config.docker.workdir],
                host_workdir=workspace,
                initialize_host_workdir=True,
                isolate_tmp=True,
                network_disabled=config.agent_network_disabled,
                masked_container_paths=["/opt/miniconda3/pkgs"],
            )
            try:
                cache.ensure(
                    env=env,
                    repository_dir=workspace,
                    sif_sha256=case.image.sif_sha256,
                    base_commit=case.base_commit,
                    instance_id=instance_id,
                    timeout=config.plan.timeout,
                )
            finally:
                env.cleanup()
        records.append({"instance_id": instance_id, "status": "prepared"})
        print(json.dumps(records[-1], sort_keys=True), flush=True)
    summary = {
        "schema_version": 1,
        "mode": "polybench_repository_history_preheat",
        "config": str(config.config_path),
        "selection_sha256": file_sha256(config.selection_manifest),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "check_only": bool(args.check_only),
        "records": records,
    }
    if not args.check_only:
        atomic_json(config.run_dir / "history_preheat_summary.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
