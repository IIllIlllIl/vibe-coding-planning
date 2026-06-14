"""CLI for GEPA Checker rule optimization."""

from __future__ import annotations

import argparse
import logging

from src.optimization.config import load_optimization_config
from src.optimization.runner import run_optimization


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    run_optimization(load_optimization_config(args.config))
