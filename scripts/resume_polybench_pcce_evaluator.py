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


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repair-id", required=True)
    args = parser.parse_args()
    result = resume_polybench_pcce_evaluator(
        load_polybench_pcce_config(args.config), repair_id=args.repair_id
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
