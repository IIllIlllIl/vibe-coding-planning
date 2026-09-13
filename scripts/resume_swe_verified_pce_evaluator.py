#!/usr/bin/env python3
"""Advance an evaluator-only replay of a preserved SWE-Verified PCE run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.swe_verified_pce.config import load_swe_verified_pce_config  # noqa: E402
from src.swe_verified_pce.evaluator_resume import (  # noqa: E402
    resume_swe_verified_pce_evaluator,
)
from src.optimization.hpc.task_batch import atomic_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repair-id", required=True)
    parser.add_argument("--instance-id", action="append", default=[])
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.repair_id):
        parser.error("--repair-id must match [A-Za-z0-9_.-]+")
    config = load_swe_verified_pce_config(args.config)
    status_path = (
        config.run_dir / "evaluator_repairs" / args.repair_id / "controller_status.json"
    )
    status = {
        "schema_version": 1,
        "mode": "swe_verified_pce_evaluator_resume",
        "repair_id": args.repair_id,
        "status": "running",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(status_path, status)
    try:
        result = resume_swe_verified_pce_evaluator(
            config,
            repair_id=args.repair_id,
            instance_ids=args.instance_id or None,
        )
    except Exception as exc:
        atomic_json(
            status_path,
            {
                **status,
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
    if result is None:
        atomic_json(
            status_path,
            {
                **status,
                "status": "waiting_workers",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        print("SWE-Verified evaluator repair yielded after durable asynchronous work.")
    else:
        atomic_json(
            status_path,
            {
                **status,
                "status": "completed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        print(
            "SWE-Verified evaluator repair finished: "
            f"evaluated={result['evaluated_instances']} "
            f"resolved={result['resolved']} unresolved={result['unresolved']} "
            f"unknown={result['unknown']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
