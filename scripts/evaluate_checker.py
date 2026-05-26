"""Batch runner for Plan-Checker-Code evaluation on SWE-bench Pro Python instances.

Computes TP/FP/FN/TN metrics by comparing checker predictions with actual
code execution results.

Usage:
    conda activate mini-swe
    python scripts/evaluate_checker.py --config config.yaml --output output/checker_eval/run1
    python scripts/evaluate_checker.py --config config.yaml --instance django__django-12345
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import Config, load_config
from src.pipeline_check import run_instance

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Configure root logger for batch output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute TP/FP/FN/TN and derived metrics from per-instance results.

    Args:
        results: List of result dicts, each with ``check_result`` and
            ``test_results`` keys.

    Returns:
        Dict with counts and metrics.
    """
    tp = fp = fn = tn = 0

    for r in results:
        check_passed = r.get("check_result", {}).get("passed", False)
        resolved = r.get("test_results", {}).get("resolved", False)

        if check_passed and resolved:
            tp += 1
        elif check_passed and not resolved:
            fp += 1
        elif not check_passed and resolved:
            fn += 1
        else:
            tn += 1

    total = tp + fp + fn + tn
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total": total,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _collect_violation_stats(
    results: list[dict[str, Any]], filter_fn: Any
) -> dict[str, int]:
    """Collect violation frequency statistics for cases matching filter_fn."""
    stats: dict[str, int] = {}
    for r in results:
        if not filter_fn(r):
            continue
        for v in r.get("check_result", {}).get("violations", []):
            rule_text = v.get("rule", "")
            if rule_text:
                stats[rule_text] = stats.get(rule_text, 0) + 1
    return stats


def _save_results(
    output_dir: Path,
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> Path:
    """Save aggregated results and metrics to output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save per-instance summaries
    summary = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "instances": [
            {
                "instance_id": r.get("instance_id", "unknown"),
                "check_passed": r.get("check_result", {}).get("passed"),
                "resolved": r.get("test_results", {}).get("resolved"),
                "violation_count": len(
                    r.get("check_result", {}).get("violations", [])
                ),
            }
            for r in results
        ],
    }

    # Top violations in FP cases (false confidence)
    fp_violations = _collect_violation_stats(
        results, lambda r: r.get("check_result", {}).get("passed") is True
        and r.get("test_results", {}).get("resolved") is False
    )
    summary["fp_violations_top10"] = sorted(
        fp_violations.items(), key=lambda x: x[1], reverse=True
    )[:10]

    # Top violations in FN cases (false rejection)
    fn_violations = _collect_violation_stats(
        results, lambda r: r.get("check_result", {}).get("passed") is False
        and r.get("test_results", {}).get("resolved") is True
    )
    summary["fn_violations_top10"] = sorted(
        fn_violations.items(), key=lambda x: x[1], reverse=True
    )[:10]

    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Results saved to %s", results_path)
    return results_path


def _load_instance_ids(path: str) -> list[str]:
    """Load instance IDs from a JSON manifest or plain text file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Instance list not found: {path}")

    text = p.read_text(encoding="utf-8")
    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, dict):
            # Manifest format with cases
            cases = data.get("cases", [])
            return [str(c.get("instance_id", c)) for c in cases]
    except json.JSONDecodeError:
        pass

    # Fallback: one ID per line
    return [line.strip() for line in text.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Evaluate plan checker on SWE-bench Pro Python instances."
    )
    parser.add_argument(
        "--config", required=True, help="Path to config.yaml"
    )
    parser.add_argument(
        "--output",
        default="./output/checker_eval/run1",
        help="Output directory for evaluation results",
    )
    parser.add_argument(
        "--instances",
        help="Path to instance list JSON or text file",
    )
    parser.add_argument(
        "--instance",
        help="Run a single instance ID (for dry-run)",
    )
    parser.add_argument(
        "--dataset",
        default="SWE-bench/SWE-bench_Pro",
        help="Dataset to load instances from",
    )
    args = parser.parse_args(argv)

    _setup_logging()

    # Load config
    config = load_config(args.config)

    # Override dataset for Pro evaluation
    # We need to reconstruct the config with the new dataset
    system_dict = {
        "n": 1,
        "optimization_info_level": config.system.optimization_info_level,
        "model": config.system.model,
        "api_base": config.system.api_base,
        "dataset": args.dataset,
        "instances": [],
        "output_dir": config.system.output_dir,
        "batch_id": f"checker_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "skip_completed_rounds": False,
    }

    from src.config import (
        AgentConfig,
        CheckerConfig,
        DockerConfig,
        EvaluatorConfig,
        PromptConfig,
        SystemConfig,
    )

    config = Config(
        system=SystemConfig(**system_dict),
        prompts=config.prompts,
        docker=config.docker,
        agent=config.agent,
        evaluator=config.evaluator,
        checker=CheckerConfig(
            enabled=True,
            rules_path=config.checker.rules_path,
            model=config.checker.model,
            api_base=config.checker.api_base,
            max_steps=config.checker.max_steps,
            cost_limit=config.checker.cost_limit,
        ),
        analysis=config.analysis,
        api_key=config.api_key,
        analysis_api_key=config.analysis_api_key,
    )

    # Determine instance list
    if args.instance:
        instance_ids = [args.instance]
    elif args.instances:
        instance_ids = _load_instance_ids(args.instances)
    else:
        # Default: read from config system.instances
        instance_ids = config.system.instances

    if not instance_ids:
        logger.error("No instances specified. Use --instance, --instances, or config.system.instances")
        return 1

    logger.info("=== Checker eval start ===")
    logger.info("Plan-Checker-Code Evaluation")
    logger.info("Dataset: %s", args.dataset)
    logger.info("Instances: %d", len(instance_ids))
    logger.info("Checker model: %s", config.checker.model)
    logger.info("Checker rules: %s", config.checker.rules_path)
    logger.info("=" * 60)

    output_dir = Path(args.output_dir)
    results: list[dict[str, Any]] = []

    for i, instance_id in enumerate(instance_ids, 1):
        logger.info("[%d/%d] Processing %s", i, len(instance_ids), instance_id)
        try:
            result = run_instance(instance_id, config)
            results.append(result)

            # Save per-instance result
            inst_dir = output_dir / "instances" / instance_id
            inst_dir.mkdir(parents=True, exist_ok=True)
            inst_path = inst_dir / "result.json"
            inst_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("[%s] Failed: %s", instance_id, exc)
            results.append({
                "instance_id": instance_id,
                "check_result": {"passed": False, "violations": [], "overall_assessment": f"Pipeline error: {exc}"},
                "test_results": {"resolved": False},
            })

    # Compute and save metrics
    metrics = _compute_metrics(results)
    logger.info("=" * 60)
    logger.info("Metrics")
    logger.info("  TP: %d  FP: %d  FN: %d  TN: %d", metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"])
    logger.info("  Accuracy:  %.3f", metrics["accuracy"])
    logger.info("  Precision: %.3f", metrics["precision"])
    logger.info("  Recall:    %.3f", metrics["recall"])
    logger.info("  F1:        %.3f", metrics["f1"])
    logger.info("=" * 60)

    _save_results(output_dir, results, metrics)
    logger.info("=== Checker eval end ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
