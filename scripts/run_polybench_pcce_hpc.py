#!/usr/bin/env python3
"""Advance one resume-safe PolyBench PCCE controller slice."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.polybench_pcce.config import load_polybench_pcce_config  # noqa: E402
from src.polybench_pcce.controller import run_polybench_pcce  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    result = run_polybench_pcce(load_polybench_pcce_config(args.config))
    if result is None:
        print("PolyBench PCCE controller yielded after durable asynchronous work.")
    else:
        print(
            "PolyBench PCCE finished: "
            f"status={result['status']} outcomes={result['method_outcomes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
