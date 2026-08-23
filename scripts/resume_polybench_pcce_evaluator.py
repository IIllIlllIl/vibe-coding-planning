#!/usr/bin/env python3
"""Advance an Evaluate-only repair of a preserved PolyBench PCCE run."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.polybench_pcce.config import load_polybench_pcce_config  # noqa: E402
from src.polybench_pcce.evaluator_resume import (  # noqa: E402
    resume_polybench_pcce_evaluator,
)
from src.polybench_pce.evaluator_resume import load_evaluator_repair_subset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repair-id", required=True)
    parser.add_argument("--instance-ids-file", type=Path)
    args = parser.parse_args()
    config = load_polybench_pcce_config(args.config)
    instance_ids = None
    if args.instance_ids_file:
        instance_ids = load_evaluator_repair_subset(
            args.instance_ids_file,
            expected_dependency_manifest_sha256=(
                config.pce.dependency_cache.manifest_sha256
                if config.pce.dependency_cache is not None
                else None
            ),
        )
    result = resume_polybench_pcce_evaluator(
        config, repair_id=args.repair_id, instance_ids=instance_ids
    )
    if result is None:
        print("PolyBench PCCE evaluator repair yielded after asynchronous work.")
    else:
        print(
            "PolyBench PCCE evaluator repair finished: "
            f"evaluated={result['evaluated_instances']} "
            f"resolved={result['resolved']} unresolved={result['unresolved']} "
            f"unknown={result['unknown']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
