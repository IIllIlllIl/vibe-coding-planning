#!/usr/bin/env python3
"""Advance one resume-safe SWE-Verified PCCE controller slice."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.swe_verified_pcce.config import load_swe_verified_pcce_config  # noqa: E402
from src.swe_verified_pcce.controller import run_swe_verified_pcce  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    result = run_swe_verified_pcce(load_swe_verified_pcce_config(args.config))
    if result is None:
        print("SWE-Verified PCCE controller yielded after durable asynchronous work.")
    else:
        print(
            "SWE-Verified PCCE finished: "
            f"status={result['status']} outcomes={result['method_outcomes']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
