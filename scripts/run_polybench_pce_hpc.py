#!/usr/bin/env python3
"""Advance the resume-safe PolyBench PCE controller by one invocation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.polybench_pce.config import load_polybench_pce_config  # noqa: E402
from src.polybench_pce.controller import run_polybench_pce  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    result = run_polybench_pce(load_polybench_pce_config(args.config))
    if result is None:
        print("PolyBench PCE controller yielded after durable asynchronous work.")
    else:
        print(
            "PolyBench PCE finished: "
            f"status={result['status']} completed={result['completed_instances']} "
            f"incomplete={result['incomplete_instances']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
