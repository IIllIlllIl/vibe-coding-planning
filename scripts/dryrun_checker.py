#!/usr/bin/env python3
"""Dry-run a single instance through the Plan-Check-Code pipeline."""

from __future__ import annotations

import json
import logging
import sys

from src.config import load_config
from src.pipeline_check import run_instance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> int:
    instance_id = sys.argv[1] if len(sys.argv) > 1 else "astropy__astropy-12907"
    config = load_config("config.yaml")
    print(f"Running Plan-Check-Code for {instance_id}...")
    print(f"Checker enabled: {config.checker.enabled}")
    print(f"Checker model: {config.checker.model}")
    print(f"Rules path: {config.checker.rules_path}")
    result = run_instance(instance_id, config)
    print("\n" + "=" * 60)
    print("RESULT:")
    print(f"  check_passed: {result.get('check_result', {}).get('passed')}")
    print(f"  violations: {len(result.get('check_result', {}).get('violations', []))}")
    print(f"  resolved: {result.get('test_results', {}).get('resolved')}")
    print(f"  output_dir: {result.get('output_dir', 'N/A')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
