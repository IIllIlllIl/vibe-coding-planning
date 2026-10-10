"""Prepare SWE-chat proxy ancestor bundles with the shared ACE history cache.

Run before Agent waves. Bounded eight-case smoke preparation may run on login;
large history packing belongs on compute nodes. Never mutate shared mirrors.
"""
import argparse
from pathlib import Path
import os

from src.environment.repository_history import RepositoryHistoryCache
from src.optimization.behavioral_repository import materialize_repository_proxy
from src.optimization.behavioral_dataset import load_behavioral_snapshot
from src.optimization.behavioral_hybrid_runtime import require_evidence_sif, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


def prepare(config, *, check_only=False):
    verify_hybrid_snapshot(config)
    require_evidence_sif(config)
    cache = RepositoryHistoryCache(Path(os.path.expandvars(config.hybrid.repository_history_cache_dir)))
    train, validation = load_behavioral_snapshot(config.dataset_snapshot)
    if not check_only and not os.environ.get("SLURM_JOB_ID") and len(train) + len(validation) > 8:
        raise ValueError("login history preparation is limited to eight smoke cases; use compute for larger sets")
    records = []
    for case in train + validation:
        existing = cache.validate(sif_sha256=config.hybrid.evidence_sif_sha256,
                                  base_commit=case.repository.proxy_commit)
        if existing is None and not check_only:
            from minisweagent.environments.local import LocalEnvironment
            mirror = config.behavioral_repository.repositories_root / case.audit_provenance["mirror_relpath"]
            with materialize_repository_proxy(mirror_path=mirror, proxy_commit=case.repository.proxy_commit,
                    workspace_root=config.behavioral_repository.workspace_root / "preheat") as repository:
                existing = cache.ensure(env=LocalEnvironment(cwd=str(repository), timeout=300),
                    repository_dir=repository, sif_sha256=config.hybrid.evidence_sif_sha256,
                    base_commit=case.repository.proxy_commit, instance_id=case.instance_id, timeout=300)
        records.append({"instance_id": case.instance_id, "base_commit": case.repository.proxy_commit,
                        "status": "prepared" if existing else "missing",
                        "manifest": existing[1] if existing else None})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    config = load_optimization_config(args.config, require_api_keys=False)
    records = prepare(config, check_only=args.check_only)
    atomic_json(args.report, {"records": records, "complete": all(r["status"] == "prepared" for r in records)})
    return 0 if all(r["status"] == "prepared" for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
