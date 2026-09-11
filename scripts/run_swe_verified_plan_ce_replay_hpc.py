#!/usr/bin/env python3
"""Advance one recovered-Plan SWE-Verified Code+Evaluate replay slice."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.swe_verified_pce.config import load_swe_verified_pce_config  # noqa: E402
from src.swe_verified_pce.plan_replay import (  # noqa: E402
    run_recovered_plan_ce_replay,
)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] != "--config":
        raise SystemExit("usage: run_swe_verified_plan_ce_replay_hpc.py --config PATH")
    config_path = Path(sys.argv[2]).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    replay = Path(str((raw.get("paths") or {})["replay_manifest"]))
    replay = replay if replay.is_absolute() else root / replay
    result = run_recovered_plan_ce_replay(
        load_swe_verified_pce_config(config_path), replay
    )
    print(
        json.dumps(
            {"status": "yielded"} if result is None else result,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
