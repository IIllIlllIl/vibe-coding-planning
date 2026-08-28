"""Run the reproducible four-arm PolyBench checker-only comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import Config, load_config  # noqa: E402
from src.output.json_io import read_jsonl, write_json  # noqa: E402
from scripts.internal.evaluate_checker import run_checker_only  # noqa: E402

DEFAULT_INPUT = Path(
    "output/SWE-PolyBench/polybench-pct-checker-datasets/"
    "20260609_198_cdf4d414e401/cases.jsonl"
)
DEFAULT_OUTPUT = Path("output/checker_eval/polybench-flash-pro-kimi-baseline")
DEFAULT_RESUME_SOURCE = Path(
    "output/checker_eval/polybench-flash-pro-baseline"
)
CHECKER_MODEL = "deepseek-v4-flash"
DEFAULT_PARALLEL = 3
DEFAULT_MAX_CACHED_IMAGES = 6
RECOVERY_ARM_NAMES = ("no_rules", "pro_rules", "kimi_rules")
BASELINE_RECOVERY_MAX_STEPS = 200
BASELINE_RECOVERY_COST_LIMIT = 6.0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {item["instance_id"]: item for item in read_jsonl(path)}


def seed_resume_predictions(source: Path, target: Path) -> dict[str, int]:
    """Copy only completed per-instance outputs from a prior experiment."""
    counts: dict[str, int] = {}
    if not source.is_dir() or source == target:
        return counts
    for name in ARM_NAMES:
        source_instances = source / name / "instances"
        target_instances = target / name / "instances"
        copied = 0
        if source_instances.is_dir():
            for prediction_path in source_instances.glob("*/prediction.json"):
                source_instance = prediction_path.parent
                target_instance = target_instances / source_instance.name
                if target_instance.exists():
                    continue
                target_instance.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_instance, target_instance)
                copied += 1
        counts[name] = copied
    return counts


def _pairwise(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    common = sorted(set(left) & set(right))
    same = left_only_correct = right_only_correct = both_correct = 0
    left_pass_right_fail: list[str] = []
    left_fail_right_pass: list[str] = []
    for instance_id in common:
        left_pass = left[instance_id]["check_result"]["passed"]
        right_pass = right[instance_id]["check_result"]["passed"]
        resolved = left[instance_id]["test_results"]["resolved"]
        same += left_pass == right_pass
        left_correct = left_pass == resolved
        right_correct = right_pass == resolved
        both_correct += left_correct and right_correct
        left_only_correct += left_correct and not right_correct
        right_only_correct += right_correct and not left_correct
        if left_pass and not right_pass:
            left_pass_right_fail.append(instance_id)
        elif right_pass and not left_pass:
            left_fail_right_pass.append(instance_id)
    return {
        "common_predictions": len(common),
        "same_prediction": same,
        "agreement_rate": same / len(common) if common else 0.0,
        "both_correct": both_correct,
        "left_only_correct": left_only_correct,
        "right_only_correct": right_only_correct,
        "left_pass_right_fail": left_pass_right_fail,
        "left_fail_right_pass": left_fail_right_pass,
    }


ARM_NAMES = ("flash_rules", "no_rules", "pro_rules", "kimi_rules")
RULE_ARM_NAMES = ("flash_rules", "pro_rules", "kimi_rules")


def build_comparison_report(output_dir: Path) -> dict[str, Any]:
    names = ARM_NAMES
    summaries = {
        name: json.loads((output_dir / name / "results.json").read_text())
        for name in names
    }
    predictions = {
        name: _prediction_map(output_dir / name / "predictions.jsonl")
        for name in names
    }
    baseline = summaries["no_rules"]["metrics"]
    metric_names = (
        "accuracy",
        "precision",
        "recall",
        "f1",
        "specificity",
        "balanced_accuracy",
        "mcc",
        "checker_pass_rate",
    )
    report = {
        "arms": {name: summaries[name]["metrics"] for name in names},
        "delta_vs_no_rules": {
            name: {
                metric: summaries[name]["metrics"][metric] - baseline[metric]
                for metric in metric_names
            }
            for name in RULE_ARM_NAMES
        },
        "pairwise": {
            f"{left}_vs_{right}": _pairwise(
                predictions[left], predictions[right]
            )
            for index, left in enumerate(names)
            for right in names[index + 1 :]
        },
    }
    write_json(output_dir / "comparison_report.json", report)

    lines = [
        "# Checker-only comparison",
        "",
        "| Arm | N | Errors | Accuracy | Precision | Recall | F1 | MCC | Pass rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in names:
        metrics = report["arms"][name]
        lines.append(
            f"| {name} | {metrics['total']} | {metrics['checker_errors']} | "
            f"{metrics['accuracy']:.4f} | {metrics['precision']:.4f} | "
            f"{metrics['recall']:.4f} | {metrics['f1']:.4f} | "
            f"{metrics['mcc']:.4f} | {metrics['checker_pass_rate']:.4f} |"
        )
    (output_dir / "comparison_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return report


def _arm_config(
    config: Config,
    *,
    rules_path: Path | None,
    baseline: bool,
    recovery: bool = False,
) -> tuple[Config, str | None]:
    prompt = (
        config.prompts.check_baseline_prompt
        if baseline
        else config.prompts.check_prompt
    )
    if not prompt.strip():
        raise ValueError("checker prompt is empty")
    checker = replace(
        config.checker,
        enabled=True,
        model=CHECKER_MODEL,
        rules_path=str(rules_path or ""),
        max_steps=(
            max(config.checker.max_steps, BASELINE_RECOVERY_MAX_STEPS)
            if baseline and recovery
            else config.checker.max_steps
        ),
        cost_limit=(
            max(
                config.checker.cost_limit,
                BASELINE_RECOVERY_COST_LIMIT,
            )
            if baseline and recovery
            else config.checker.cost_limit
        ),
    )
    prompts = replace(config.prompts, check_prompt=prompt)
    return replace(config, checker=checker, prompts=prompts), ("" if baseline else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="configs/archive/pct_runs/polybench_full199_pct.yaml"
    )
    parser.add_argument("--input-results", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--flash-rules",
        type=Path,
        default=Path("output/analysis_flash/aggregated_rules.json"),
    )
    parser.add_argument(
        "--pro-rules",
        type=Path,
        default=Path("output/analysis_pro/aggregated_rules.json"),
    )
    parser.add_argument(
        "--kimi-rules",
        type=Path,
        default=Path(
            "output/analysis_kimi_opencode_60/aggregated_rules.json"
        ),
    )
    parser.add_argument(
        "--resume-source",
        type=Path,
        default=DEFAULT_RESUME_SOURCE,
        help="Prior comparison output used to seed matching arm predictions",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument(
        "--recovery",
        action="store_true",
        help=(
            "Resume only no_rules, pro_rules, and kimi_rules in that order. "
            "Completed predictions are reused; the no-rules retry budget is "
            "raised for prior LimitsExceeded cases."
        ),
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=DEFAULT_PARALLEL,
        help=(
            "Concurrent checker instances within each arm "
            f"(default: {DEFAULT_PARALLEL})"
        ),
    )
    parser.add_argument(
        "--max-cached-images",
        type=int,
        default=DEFAULT_MAX_CACHED_IMAGES,
        help=(
            "Newest project Docker images retained during the run "
            f"(default: {DEFAULT_MAX_CACHED_IMAGES})"
        ),
    )
    args = parser.parse_args()
    if args.parallel < 1:
        parser.error("--parallel must be at least 1")
    if args.max_cached_images < args.parallel:
        parser.error("--max-cached-images must be at least --parallel")

    required = (
        args.input_results,
        args.flash_rules,
        args.pro_rules,
        args.kimi_rules,
    )
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    config = load_config(args.config)
    config = replace(
        config,
        docker=replace(
            config.docker,
            delete_images_after_instance=True,
            max_cached_images=args.max_cached_images,
        ),
    )
    cases = read_jsonl(args.input_results)
    all_arms = (
        ("flash_rules", args.flash_rules, False),
        ("no_rules", None, True),
        ("pro_rules", args.pro_rules, False),
        ("kimi_rules", args.kimi_rules, False),
    )
    arms = (
        tuple(arm for arm in all_arms if arm[0] in RECOVERY_ARM_NAMES)
        if args.recovery
        else all_arms
    )
    metadata = {
        "schema_version": 1,
        "status": "planned" if args.dry_run else "running",
        "checker_model": CHECKER_MODEL,
        "config_path": str(args.config),
        "input_path": str(args.input_results),
        "input_sha256": _sha256(args.input_results),
        "case_count": len(cases),
        "arms": {},
    }
    for name, rules_path, baseline in all_arms:
        arm_config, _ = _arm_config(
            config, rules_path=rules_path, baseline=baseline
        )
        metadata["arms"][name] = {
            "status": "planned",
            "rules_path": str(rules_path) if rules_path else None,
            "rules_sha256": _sha256(rules_path) if rules_path else None,
            "prompt_sha256": _text_sha256(arm_config.prompts.check_prompt),
        }
    fingerprint_data = json.dumps(metadata, sort_keys=True).encode()
    metadata["experiment_fingerprint"] = hashlib.sha256(
        fingerprint_data
    ).hexdigest()
    metadata["execution_arms"] = [name for name, _, _ in arms]

    if args.dry_run:
        metadata["parallel_workers"] = args.parallel
        metadata["max_cached_images"] = args.max_cached_images
        print(json.dumps(metadata, indent=2))
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    seeded = seed_resume_predictions(args.resume_source, args.output)
    if any(seeded.values()):
        print(f"Seeded prior predictions: {seeded}", flush=True)
    experiment_path = args.output / "experiment.json"
    if experiment_path.is_file():
        existing = json.loads(experiment_path.read_text())
        if (
            existing.get("experiment_fingerprint")
            != metadata["experiment_fingerprint"]
        ):
            raise ValueError(
                f"Output contains a different experiment: {args.output}"
            )
        metadata = existing
        metadata["status"] = "running"
    else:
        metadata["started_at"] = datetime.now(timezone.utc).isoformat()
    metadata["parallel_workers"] = args.parallel
    metadata["max_cached_images"] = args.max_cached_images
    if args.recovery:
        recovery_runs = metadata.setdefault("recovery_runs", [])
        recovery_runs.append(
            {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "arms": list(RECOVERY_ARM_NAMES),
                "baseline_max_steps": BASELINE_RECOVERY_MAX_STEPS,
                "baseline_cost_limit": BASELINE_RECOVERY_COST_LIMIT,
            }
        )
    write_json(experiment_path, metadata)

    for name, rules_path, baseline in arms:
        arm_config, rules_override = _arm_config(
            config,
            rules_path=rules_path,
            baseline=baseline,
            recovery=args.recovery,
        )
        print(f"=== Checker comparison arm start: {name} ===", flush=True)
        metadata["arms"][name]["status"] = "running"
        write_json(experiment_path, metadata)
        results, errors = run_checker_only(
            config=arm_config,
            input_path=args.input_results,
            output_dir=args.output / name,
            rules_text_override=rules_override,
            resume=not args.no_resume,
            max_workers=args.parallel,
        )
        metadata["arms"][name].update(
            {
                "status": "complete",
                "predictions": len(results),
                "errors": len(errors),
            }
        )
        write_json(experiment_path, metadata)
        print(f"=== Checker comparison arm end: {name} ===", flush=True)

    build_comparison_report(args.output)
    metadata["status"] = "complete"
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(experiment_path, metadata)
    print("=== Checker comparison end ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
