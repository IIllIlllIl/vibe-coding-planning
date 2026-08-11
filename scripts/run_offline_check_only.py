#!/usr/bin/env python3
"""Run the additive Offline Checker-only controller."""

from __future__ import annotations

import argparse

from src.offline_check_only.config import load_check_only_config
from src.offline_check_only.runner import run_check_only


def main() -> None:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run_check_only(load_check_only_config(args.config))


if __name__ == "__main__":
    main()
