"""Run the reproducible three-arm PolyBench checker-only comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate_checker import _read_jsonl, run_checker_only  # noqa: E402
from src.config import Config, load_config  # noqa: E402

DEFAULT_INPUT = Path(
    "output/SWE-PolyBench/polybench-pct-checker-datasets/"
    "20260609_198_cdf4d414e401/cases.jsonl"
)
DEFAULT_OUTPUT = Path("output/checker_eval/polybench-flash-pro-baseline")
CHECKER_MODEL = "deepseek-v4-flash"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return {item["instance_id"]: item for item in _read_jsonl(path)}


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


def build_comparison_report(output_dir: Path) -> dict[str, Any]:
    names = ("flash_rules", "pro_rules", "no_rules")
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
            for name in ("flash_rules", "pro_rules")
        },
        "pairwise": {
            "flash_rules_vs_no_rules": _pairwise(
                predictions["flash_rules"], predictions["no_rules"]
            ),
            "pro_rules_vs_no_rules": _pairwise(
                predictions["pro_rules"], predictions["no_rules"]
            ),
            "flash_rules_vs_pro_rules": _pairwise(
                predictions["flash_rules"], predictions["pro_rules"]
            ),
        },
    }
    _write_json(output_dir / "comparison_report.json", report)

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
    config: Config, *, rules_path: Path | None, baseline: bool
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
    )
    prompts = replace(config.prompts, check_prompt=prompt)
    return replace(config, checker=checker, prompts=prompts), ("" if baseline else None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/polybench_full199_pct.yaml")
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    required = (args.input_results, args.flash_rules, args.pro_rules)
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(path)
    config = load_config(args.config)
    cases = _read_jsonl(args.input_results)
    arms = (
        ("flash_rules", args.flash_rules, False),
        ("pro_rules", args.pro_rules, False),
        ("no_rules", None, True),
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
    for name, rules_path, baseline in arms:
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

    if args.dry_run:
        print(json.dumps(metadata, indent=2))
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
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
    _write_json(experiment_path, metadata)

    for name, rules_path, baseline in arms:
        arm_config, rules_override = _arm_config(
            config, rules_path=rules_path, baseline=baseline
        )
        print(f"=== Checker comparison arm start: {name} ===", flush=True)
        metadata["arms"][name]["status"] = "running"
        _write_json(experiment_path, metadata)
        results, errors = run_checker_only(
            config=arm_config,
            input_path=args.input_results,
            output_dir=args.output / name,
            rules_text_override=rules_override,
            resume=not args.no_resume,
        )
        metadata["arms"][name].update(
            {
                "status": "complete",
                "predictions": len(results),
                "errors": len(errors),
            }
        )
        _write_json(experiment_path, metadata)
        print(f"=== Checker comparison arm end: {name} ===", flush=True)

    build_comparison_report(args.output)
    metadata["status"] = "complete"
    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(experiment_path, metadata)
    print("=== Checker comparison end ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
