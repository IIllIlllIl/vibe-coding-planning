"""Tests for the checker comparison orchestration."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

spec = importlib.util.spec_from_file_location(
    "run_checker_comparison", "scripts/run_checker_comparison.py"
)
comparison = importlib.util.module_from_spec(spec)
sys.modules["run_checker_comparison"] = comparison
spec.loader.exec_module(comparison)

from src.config import Config, PromptConfig  # noqa: E402


def test_default_parallel_is_four():
    assert comparison.DEFAULT_PARALLEL == 4


def test_all_arms_force_flash_and_baseline_uses_dedicated_prompt(tmp_path):
    config = Config(
        prompts=PromptConfig(
            check_prompt="rule prompt",
            check_baseline_prompt="baseline prompt",
        )
    )
    rules_config, rules_override = comparison._arm_config(
        config, rules_path=tmp_path / "rules.json", baseline=False
    )
    baseline_config, baseline_override = comparison._arm_config(
        config, rules_path=None, baseline=True
    )

    assert rules_config.checker.model == "deepseek-v4-flash"
    assert baseline_config.checker.model == "deepseek-v4-flash"
    assert rules_config.prompts.check_prompt == "rule prompt"
    assert baseline_config.prompts.check_prompt == "baseline prompt"
    assert rules_override is None
    assert baseline_override == ""


def _write_arm(output: Path, name: str, passed: bool) -> None:
    arm = output / name
    arm.mkdir(parents=True)
    metrics = {
        "tp": int(passed),
        "fp": 0,
        "fn": int(not passed),
        "tn": 0,
        "total": 1,
        "accuracy": float(passed),
        "precision": float(passed),
        "recall": float(passed),
        "f1": float(passed),
        "specificity": 0.0,
        "balanced_accuracy": float(passed) / 2,
        "mcc": 0.0,
        "checker_pass_rate": float(passed),
        "resolved_prevalence": 1.0,
        "checker_errors": 0,
    }
    (arm / "results.json").write_text(
        json.dumps({"metrics": metrics}), encoding="utf-8"
    )
    (arm / "predictions.jsonl").write_text(
        json.dumps(
            {
                "instance_id": "repo__task-1",
                "check_result": {"passed": passed},
                "test_results": {"resolved": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_comparison_report_includes_deltas_and_pairwise(tmp_path):
    _write_arm(tmp_path, "flash_rules", True)
    _write_arm(tmp_path, "pro_rules", False)
    _write_arm(tmp_path, "no_rules", False)

    report = comparison.build_comparison_report(tmp_path)

    assert report["delta_vs_no_rules"]["flash_rules"]["accuracy"] == 1.0
    pair = report["pairwise"]["flash_rules_vs_no_rules"]
    assert pair["left_only_correct"] == 1
    assert pair["left_pass_right_fail"] == ["repo__task-1"]
    assert (tmp_path / "comparison_report.md").is_file()
