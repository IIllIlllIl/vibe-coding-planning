#!/usr/bin/env python3
"""Advance one resume-safe Pro PCE controller slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.swe_bench_pro_pce.config import load_swe_bench_pro_pce_config  # noqa: E402
from src.swe_bench_pro_pce.controller import run_swe_bench_pro_pce  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    result = run_swe_bench_pro_pce(load_swe_bench_pro_pce_config(parser.parse_args().config))
    print("Pro PCE controller yielded." if result is None else json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
