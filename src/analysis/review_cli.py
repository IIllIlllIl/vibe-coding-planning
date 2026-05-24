"""CLI entry point for LLM-based rule quality review.

Usage:
    python -m src.analysis.review_cli \
        --data-dir output/SWE-bench_Verified/reflect_success_cases \
        --output-dir output/analysis_pro \
        --model deepseek-v4-pro \
        [--instance inst1 --instance inst2]

Logs:
    logs/review_run.log (via tee from the tmux session)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import dataclasses

from src.analysis.case_loader import load_cases
from src.analysis.output import AnalysisOutputWriter
from src.analysis.reviewer_agent import run_reviewer
from src.config import load_config
from src.exceptions import FatalError

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point for batch rule review."""
    _setup_logging()

    parser = argparse.ArgumentParser(
        description="LLM-based quality review of extracted contrastive rules"
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--data-dir",
        default="output/SWE-bench_Verified/reflect_success_cases",
        help="Path to reflect_success_cases directory",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Analysis output directory (contains per_case/*.json)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override the review model (e.g. deepseek-v4-pro)",
    )
    parser.add_argument(
        "--instance",
        action="append",
        default=None,
        help="Review specific instance(s) only (can be used multiple times)",
    )
    args = parser.parse_args(argv)

    # Load configuration
    try:
        config = load_config(args.config)
    except FatalError as exc:
        logger.error("Failed to load config: %s", exc)
        return 1

    # Allow model override from CLI
    if args.model:
        config = dataclasses.replace(
            config,
            analysis=dataclasses.replace(config.analysis, model=args.model),
        )
        logger.info("Overriding review model to %s", args.model)

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        logger.error("Data directory not found: %s", data_dir)
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else Path(config.analysis.output_dir)
    per_case_dir = output_dir / "per_case"
    if not per_case_dir.exists():
        logger.error("No per_case directory found at %s", per_case_dir)
        return 1

    # Load cases
    try:
        all_cases = load_cases(data_dir)
    except FileNotFoundError as exc:
        logger.error("Failed to load cases: %s", exc)
        return 1

    # Filter to requested instances
    if args.instance:
        requested = set(args.instance)
        cases = [c for c in all_cases if c.instance_id in requested]
        missing = requested - {c.instance_id for c in cases}
        if missing:
            logger.warning("Requested instances not found in data: %s", sorted(missing))
    else:
        cases = all_cases

    logger.info("Running rule review for %d case(s)", len(cases))

    # Prepare output writer for trajectories
    writer = AnalysisOutputWriter(output_dir)

    all_results: dict[str, dict] = {}
    success_count = 0
    fail_count = 0

    for case in cases:
        logger.info("=" * 60)
        logger.info("START review for %s", case.instance_id)

        # Load existing rule from per_case result
        result_file = per_case_dir / f"{case.instance_id}.json"
        if not result_file.exists():
            logger.warning("No result file for %s, skipping review", case.instance_id)
            review_result = _make_error_review("No result file found")
            all_results[case.instance_id] = review_result
            fail_count += 1
            continue

        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Cannot read result file for %s: %s", case.instance_id, exc)
            review_result = _make_error_review(f"Cannot read result file: {exc}")
            all_results[case.instance_id] = review_result
            fail_count += 1
            continue

        rule_text = data.get("rule", "")
        if not rule_text:
            logger.warning("Empty rule for %s, skipping LLM review", case.instance_id)
            review_result = _make_error_review("Empty rule")
            all_results[case.instance_id] = review_result
            fail_count += 1
            continue

        try:
            review_result, trajectory = run_reviewer(
                config=config,
                case=case,
                data_base_dir=str(data_dir),
                rule_text=rule_text,
                model_name=args.model,
            )

            # Save trajectory
            writer.save_trajectory(f"review_{case.instance_id}", trajectory)

            all_results[case.instance_id] = review_result
            logger.info(
                "DONE review for %s score=%d passed=%s",
                case.instance_id,
                review_result["score"],
                review_result["passed"],
            )
            success_count += 1

        except Exception as exc:
            logger.error("Review failed for %s: %s", case.instance_id, exc)
            review_result = _make_error_review(str(exc))
            all_results[case.instance_id] = review_result
            fail_count += 1

    # Save aggregated review results
    review_results_path = output_dir / "review_results.json"
    review_results_path.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Review results saved to %s", review_results_path)

    logger.info(
        "=" * 60 + "\nReview finished: %d succeeded, %d failed out of %d cases",
        success_count,
        fail_count,
        len(cases),
    )
    logger.info("=== Review end ===")
    return 0 if fail_count == 0 else 1


def _make_error_review(error_msg: str) -> dict:
    """Return a default failed review result for error cases."""
    return {
        "passed": False,
        "score": 0,
        "feedback": error_msg,
        "issues": [error_msg],
        "improvement_suggestions": "Retry rule extraction or review.",
    }


if __name__ == "__main__":
    sys.exit(main())
