#!/usr/bin/env python3
"""Advance an evaluator-only repair of a preserved PolyBench PCE run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.polybench_pce.config import load_polybench_pce_config  # noqa: E402
from src.polybench_pce.evaluator_resume import (  # noqa: E402
    load_evaluator_repair_subset,
    resume_polybench_pce_evaluator,
)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repair-id", required=True)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--instance-ids-file", type=Path)
    args = parser.parse_args()
    if args.instance_id and args.instance_ids_file:
        parser.error("--instance-id and --instance-ids-file are mutually exclusive")
    config = load_polybench_pce_config(args.config)
    instance_ids = args.instance_id or None
    if args.instance_ids_file:
        instance_ids = load_evaluator_repair_subset(
            args.instance_ids_file,
            expected_dependency_manifest_sha256=(
                config.dependency_cache.manifest_sha256
                if config.dependency_cache is not None
                else None
            ),
        )
    result = resume_polybench_pce_evaluator(
        config,
        repair_id=args.repair_id,
        instance_ids=instance_ids,
    )
    if result is None:
        print("PolyBench evaluator repair yielded after durable asynchronous work.")
    else:
        print(
            "PolyBench evaluator repair finished: "
            f"evaluated={result['evaluated_instances']} "
            f"resolved={result['resolved']} unresolved={result['unresolved']} "
            f"unknown={result['unknown']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
