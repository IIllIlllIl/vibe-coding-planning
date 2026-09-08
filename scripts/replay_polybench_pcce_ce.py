#!/usr/bin/env python3
"""Run Code+Evaluate from frozen accepted PCCE Plans."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.polybench_pcce.ce_replay import run_ce_replay  # noqa: E402
from src.polybench_pcce.config import load_polybench_pcce_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    result = run_ce_replay(
        load_polybench_pcce_config(args.config),
        replay_id=args.replay_id,
        manifest_path=args.manifest,
    )
    print("CE replay yielded." if result is None else f"CE replay finished: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
