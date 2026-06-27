"""CLI entry point for contrastive rule extraction.

Usage:
    python -m src.analysis --input output/SWE-bench_Verified/reflect_success_cases
    python -m src.analysis --instance django__django-11433
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import dataclasses

from src.analysis.aggregation_agent import aggregate_with_config
from src.analysis.case_loader import load_cases
from src.analysis.contrastive_agent import run as run_agent
from src.analysis.opencode_agent import aggregate as aggregate_with_opencode
from src.analysis.opencode_agent import run as run_opencode_agent
from src.analysis.output import AnalysisOutputWriter
from src.analysis.rule_postprocess import postprocess_per_case_dir
from src.config import load_config
from src.exceptions import FatalError, TaskError

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="Contrastive rule extraction from reflect-success cases"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--input",
        default="output/SWE-bench_Verified/reflect_success_cases",
        help="Path to reflect_success_cases directory",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory (overrides config.analysis.output_dir)",
    )
    parser.add_argument(
        "--instance",
        default=None,
        help="Run analysis for a single instance ID only (for debugging)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the analysis model (e.g. deepseek-v4-pro)",
    )
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Run rule aggregation instead of per-case extraction. "
             "--input is treated as the per_case directory.",
    )
    parser.add_argument(
        "--postprocess",
        action="store_true",
        help="Rewrite extracted per-case rules into canonical format. "
             "--input is treated as the original per_case directory; output is "
             "<output>/per_case_postprocessed.",
    )
    parser.add_argument(
        "--postprocess-data-dir",
        default="output/SWE-bench_Verified/reflect_success_cases",
        help="Reflect-success cases directory used as context for --postprocess.",
    )
    args = parser.parse_args(argv)

    # Load configuration
    try:
        config = load_config(args.config, require_api_key=False)
    except FatalError as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    # Allow model override from CLI for comparative experiments
    if args.model:
        config = dataclasses.replace(
            config,
            analysis=dataclasses.replace(config.analysis, model=args.model),
        )
        logger.info("Overriding analysis model to %s", args.model)

    data_dir = Path(args.input).resolve()
    if not data_dir.exists():
        logger.error("Input directory not found: %s", data_dir)
        return 1

    output_dir = Path(args.output) if args.output else Path(config.analysis.output_dir)

    # -----------------------------------------------------------------------
    # Postprocess mode: preserve original per_case files and write a repaired
    # per_case_postprocessed directory for aggregation.
    # -----------------------------------------------------------------------
    if args.postprocess:
        postprocess_output = output_dir / "per_case_postprocessed"
        try:
            stats = postprocess_per_case_dir(
                per_case_dir=data_dir,
                output_dir=postprocess_output,
                config=config,
                data_base_dir=args.postprocess_data_dir,
            )
            logger.info(
                "Postprocess complete: copied=%d repaired=%d failed=%d skipped_empty=%d -> %s",
                stats["copied_valid"],
                stats["repaired"],
                stats["failed"],
                stats["skipped_empty"],
                postprocess_output,
            )
            return 0 if stats["failed"] == 0 else 1
        except Exception as exc:
            logger.error("Postprocess failed: %s", exc)
            return 1

    # -----------------------------------------------------------------------
    # Aggregation mode (Input-Aware Tree Merge)
    # -----------------------------------------------------------------------
    if args.aggregate:
        per_case_dir = data_dir
        aggregate_output = output_dir / "aggregated_rules.json"
        try:
            if config.analysis.backend == "opencode":
                result = aggregate_with_opencode(
                    per_case_dir=per_case_dir,
                    output_path=aggregate_output,
                    config=config,
                )
            else:
                result = aggregate_with_config(
                    per_case_dir=per_case_dir,
                    output_path=aggregate_output,
                    config=config,
                )
            logger.info(
                "Aggregation complete: %d always rules, %d branches -> %s",
                len(result.get("always", [])),
                len(result.get("branches", [])),
                aggregate_output,
            )
            return 0
        except Exception as exc:
            logger.error("Aggregation failed: %s", exc)
            return 1

    # -----------------------------------------------------------------------
    # Per-case contrastive analysis mode
    # -----------------------------------------------------------------------
    writer = AnalysisOutputWriter(output_dir)

    # Load cases
    try:
        all_cases = load_cases(data_dir)
    except FileNotFoundError as exc:
        logger.error("Failed to load cases: %s", exc)
        return 1

    # Filter to single instance if requested
    if args.instance:
        cases = [c for c in all_cases if c.instance_id == args.instance]
        if not cases:
            logger.error("Instance %s not found in %s", args.instance, data_dir)
            return 1
    else:
        cases = all_cases

    logger.info("Running contrastive analysis for %d case(s)", len(cases))

    success_count = 0
    fail_count = 0

    for case in cases:
        logger.info("=" * 60)
        logger.info("[%s] Starting analysis", case.instance_id)

        try:
            agent_run = (
                run_opencode_agent
                if config.analysis.backend == "opencode"
                else run_agent
            )
            rule_text, trajectory = agent_run(
                config=config,
                case=case,
                data_base_dir=str(data_dir),
            )

            # Validate rule format (basic check) — supports multiple rules
            lines = [ln.strip() for ln in rule_text.splitlines() if ln.strip()]
            rule_lines = [ln for ln in lines if ln.lower().startswith("when ")]
            rule_valid = bool(rule_lines) and all(" because " in ln.lower() for ln in rule_lines)

            # Save per-case result
            writer.save_result(
                instance_id=case.instance_id,
                rule=rule_text,
                rule_valid=rule_valid,
            )

            # Save trajectory
            writer.save_trajectory(case.instance_id, trajectory)

            # Append to aggregate JSONL
            writer.append_rule_jsonl(
                {
                    "instance_id": case.instance_id,
                    "rule": rule_text,
                    "rule_valid": rule_valid,
                }
            )

            logger.info(
                "[%s] Analysis complete — rule_valid=%s rule=%.80s...",
                case.instance_id,
                rule_valid,
                rule_text,
            )
            success_count += 1

        except TaskError as exc:
            logger.error("[%s] Analysis failed: %s", case.instance_id, exc)
            writer.save_result(
                instance_id=case.instance_id,
                rule="",
                rule_valid=False,
                error=str(exc),
            )
            writer.append_error_jsonl(
                {
                    "instance_id": case.instance_id,
                    "error": str(exc),
                }
            )
            fail_count += 1

    logger.info(
        "=" * 60 + "\nFinished: %d succeeded, %d failed out of %d cases",
        success_count,
        fail_count,
        len(cases),
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
