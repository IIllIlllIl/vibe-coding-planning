#!/usr/bin/env python3
"""Entry point for the ULHPC SIF preheat watchdog."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.hpc_preheat_watchdog_lib.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
