"""CLI for GEPA Checker rule optimization."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from src.optimization.config import load_optimization_config
from src.optimization.checker_stability import run_checker_stability
from src.optimization.behavioral_runner import run_behavioral_optimization
from src.optimization.online_config import load_online_optimization_config
from src.optimization.online_runner import run_online_optimization
from src.optimization.runner import run_optimization
from src.optimization.playbook_cli import run_from_config as run_playbook_from_config
from src.optimization.playbook_replay import run_curator_replay


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raw = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    if raw.get("mode") == "offline_reject_playbook_curator_replay":
        run_curator_replay(args.config)
    elif raw.get("mode") in {
        "offline_reject_playbook",
        "offline_repo_concern_playbook",
    }:
        run_playbook_from_config(args.config)
    elif raw.get("mode") == "online_planning":
        run_online_optimization(load_online_optimization_config(args.config))
    elif raw.get("mode") == "offline_checker_stability":
        diagnostic = raw.get("diagnostic") or {}
        run_checker_stability(
            load_optimization_config(args.config),
            repetitions=int(diagnostic.get("repetitions", 3)),
            config_path=Path(args.config).resolve(),
        )
    else:
        config = load_optimization_config(args.config)
        if config.task.semantics == "behavioral_plan_acceptability_v1":
            run_behavioral_optimization(config)
        else:
            run_optimization(config)
