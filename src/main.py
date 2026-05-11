"""Entry point for the plan-code-test system.

Parses CLI arguments, loads configuration, and drives the pipeline.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Sequence

from src.config import Config, load_config
from src.exceptions import FatalError
from src.pipeline import run_instance


def _setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger for console output."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Plan-Code-Test: automated plan generation and iterative optimization.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--instance",
        type=str,
        default=None,
        help="Single SWE-bench instance ID to run (overrides config file list)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help="Number of plan iterations (overrides config file value)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Output directory (overrides config file value)",
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help=(
            "Batch identifier used as a folder segment between dataset and "
            "instance (output/<dataset>/<batch_id>/<instance>/). Overrides "
            "system.batch_id from config.yaml. Required at run time — either "
            "set it here or in the config file."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return parser.parse_args(argv)


def _override_config(config: Config, args: argparse.Namespace) -> Config:
    """Apply CLI argument overrides to the loaded config."""
    # Config is frozen, so we need to rebuild with overrides.
    # For simplicity we use object.__setattr__ since the fields are simple.
    overrides: dict[str, object] = {}
    if args.n is not None:
        overrides["n"] = args.n
    if args.output_dir is not None:
        overrides["output_dir"] = args.output_dir
    if args.batch_id is not None:
        # Re-validate the CLI value with the same rules used by the YAML
        # loader so a malformed --batch-id is rejected eagerly instead of
        # silently producing a broken path.
        from src.config import validate_batch_id

        overrides["batch_id"] = validate_batch_id(args.batch_id)

    if not overrides:
        return config

    # Rebuild system config with overrides
    from dataclasses import fields
    from src.config import SystemConfig

    system_kwargs = {
        f.name: getattr(config.system, f.name)
        for f in fields(SystemConfig)
    }
    system_kwargs.update(overrides)
    new_system = SystemConfig(**system_kwargs)

    # Build new Config (frozen dataclass workaround)
    new_config = Config(
        system=new_system,
        prompts=config.prompts,
        docker=config.docker,
        agent=config.agent,
        evaluator=config.evaluator,
        api_key=config.api_key,
    )
    return new_config


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point.

    Returns:
        0 on success, 1 on fatal error.
    """
    args = parse_args(argv)
    _setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    # Verify API key is set before loading config (config.load_config also checks)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        logging.error(
            "Environment variable DEEPSEEK_API_KEY is not set. "
            "Please set it: export DEEPSEEK_API_KEY='your-key'"
        )
        return 1

    # Load configuration
    try:
        config = load_config(args.config)
    except FatalError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    # Apply CLI overrides. Re-running validation (e.g. on --batch-id) can
    # also raise FatalError, so wrap this in the same handler shape.
    try:
        config = _override_config(config, args)
    except FatalError as exc:
        logging.error("Configuration error: %s", exc)
        return 1

    # Determine instance list
    instances: list[str] = []
    if args.instance:
        instances = [args.instance]
    else:
        instances = list(config.system.instances)

    if not instances:
        logging.error("No instances specified. Use --instance or set system.instances in config.")
        return 1

    logging.info("Starting plan-code-test pipeline")
    logging.info("Model: %s", config.system.model)
    logging.info("Dataset: %s", config.system.dataset)
    logging.info("Batch: %s", config.system.batch_id)
    logging.info("Iterations (n): %d", config.system.n)
    logging.info("Instances: %s", instances)
    logging.info("Output dir: %s", config.system.output_dir)

    # Run each instance
    exit_code = 0
    for instance_id in instances:
        logging.info("=" * 60)
        logging.info("Processing instance: %s", instance_id)
        logging.info("=" * 60)

        try:
            result = run_instance(instance_id, config)
            resolved_any = any(
                p.get("test_results", {}).get("resolved") for p in result.get("plans", [])
            )
            logging.info(
                "Instance %s complete. Any resolved: %s",
                instance_id,
                resolved_any,
            )
        except FatalError as exc:
            logging.error("Fatal error on instance %s: %s", instance_id, exc)
            exit_code = 1
            break  # Stop processing further instances
        except Exception as exc:
            logging.error("Unexpected error on instance %s: %s", instance_id, exc)
            exit_code = 1
            # Continue with next instance

    logging.info("Pipeline complete. Exit code: %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
