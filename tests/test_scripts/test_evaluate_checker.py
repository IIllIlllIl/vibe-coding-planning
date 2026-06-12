"""Tests for saved-PCT checker evaluation."""

from __future__ import annotations

import json
import importlib.util
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import CheckerConfig, Config, SystemConfig  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "evaluate_checker", "scripts/internal/evaluate_checker.py"
)
evaluate_checker = importlib.util.module_from_spec(spec)
sys.modules["evaluate_checker"] = evaluate_checker
spec.loader.exec_module(evaluate_checker)

_compute_metrics = evaluate_checker._compute_metrics
_run_checker_case = evaluate_checker._run_checker_case
_select_earliest_success = evaluate_checker._select_earliest_success
_write_jsonl = evaluate_checker._write_jsonl
build_pct_checker_input = evaluate_checker.build_pct_checker_input
run_checker_only = evaluate_checker.run_checker_only
publish_pct_checker_snapshot = evaluate_checker.publish_pct_checker_snapshot


def _candidate(
    *,
    timestamp: str,
    plan_hash: str,
    resolved: bool,
    valid: bool = True,
    retry: bool = False,
    path: str = "result.json",
) -> dict:
    return {
        "plan_generated_at": timestamp,
        "plan_sha256": plan_hash,
        "resolved": resolved,
        "valid_label": valid,
        "invalid_reason": "" if valid else "evaluator error",
        "is_evaluator_retry": retry,
        "source_result_path": path,
    }


def test_selects_first_successful_pct_without_resolved_bias():
    selected = _select_earliest_success(
        [
            _candidate(
                timestamp="20260601T000000",
                plan_hash="first",
                resolved=False,
                path="run20/result.json",
            ),
            _candidate(
                timestamp="20260602T000000",
                plan_hash="second",
                resolved=True,
                path="run100/result.json",
            ),
        ]
    )
    assert selected["plan_sha256"] == "first"
    assert selected["resolved"] is False


def test_evaluator_retry_repairs_same_plan_but_is_not_new_pct():
    selected = _select_earliest_success(
        [
            _candidate(
                timestamp="20260601T000000",
                plan_hash="same",
                resolved=False,
                valid=False,
                path="original/result.json",
            ),
            _candidate(
                timestamp="20260601T000000",
                plan_hash="same",
                resolved=True,
                retry=True,
                path="retry/result.json",
            ),
            _candidate(
                timestamp="20260602T000000",
                plan_hash="later",
                resolved=False,
                path="later/result.json",
            ),
        ]
    )
    assert selected["plan_sha256"] == "same"
    assert selected["resolved"] is True


def test_evaluator_retry_candidate_keeps_original_pct_source(tmp_path):
    instance_id = "repo__task-1"
    original = tmp_path / "run20" / instance_id
    retry = tmp_path / "retry" / instance_id
    (original / "plans").mkdir(parents=True)
    (retry / "plans").mkdir(parents=True)
    plan_name = "plan_1_plan_gen_20260601T000000.md"
    for directory in (original, retry):
        (directory / "plans" / plan_name).write_text("plan", encoding="utf-8")
    (original / "result.json").write_text(
        json.dumps({"run_id": "run_20260601T010000Z", "plans": []}),
        encoding="utf-8",
    )
    retry_result = {
        "run_id": "polybench_evaluator_retry",
        "plans": [
            {
                "round": 1,
                "plan_path": f"plans/{plan_name}",
                "patch_policy": {"recovered_from": str(original)},
                "test_results": {
                    "resolved": True,
                    "error_info": None,
                    "report": {instance_id: {"patch_applied": True}},
                },
            }
        ],
    }
    (retry / "result.json").write_text(
        json.dumps(retry_result), encoding="utf-8"
    )

    candidates, _ = evaluate_checker._scan_pct_candidates(
        tmp_path, {instance_id}
    )
    candidate = candidates[instance_id][0]

    assert candidate["source_batch"] == "run20"
    assert candidate["source_result_path"] == str(original / "result.json")
    assert candidate["label_source_result_path"] == str(retry / "result.json")
    assert candidate["pct_run_id"] == "run_20260601T010000Z"


def test_original_valid_label_is_preferred_over_evaluator_retry():
    selected = _select_earliest_success(
        [
            _candidate(
                timestamp="20260601T000000",
                plan_hash="same",
                resolved=False,
                path="original/result.json",
            ),
            _candidate(
                timestamp="20260601T000000",
                plan_hash="same",
                resolved=True,
                retry=True,
                path="retry/result.json",
            ),
        ]
    )
    assert selected["resolved"] is False


def test_later_identical_plan_does_not_repair_earlier_failed_pct():
    selected = _select_earliest_success(
        [
            _candidate(
                timestamp="20260601T000000",
                plan_hash="same",
                resolved=False,
                valid=False,
                path="failed/result.json",
            ),
            _candidate(
                timestamp="20260602T000000",
                plan_hash="same",
                resolved=True,
                path="later/result.json",
            ),
        ]
    )
    assert selected["source_result_path"] == "later/result.json"


def _write_pct_result(
    root: Path,
    batch: str,
    instance_id: str,
    *,
    timestamp: str,
    plan: str,
    resolved: bool,
    patch_applied: bool = True,
) -> None:
    instance_dir = root / batch / instance_id
    plans_dir = instance_dir / "plans"
    plans_dir.mkdir(parents=True)
    plan_name = f"plan_1_plan_gen_{timestamp}.md"
    (plans_dir / plan_name).write_text(plan, encoding="utf-8")
    result = {
        "run_id": f"run_{timestamp}Z",
        "plans": [
            {
                "round": 1,
                "plan_path": f"plans/{plan_name}",
                "test_results": {
                    "resolved": resolved,
                    "error_info": None,
                    "report": {
                        instance_id: {"patch_applied": patch_applied}
                    },
                },
            }
        ],
    }
    (instance_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")


@patch("evaluate_checker.InstanceLoader")
def test_build_input_is_ordered_and_contains_existing_checker_inputs(
    mock_loader_cls, tmp_path
):
    root = tmp_path / "pct"
    _write_pct_result(
        root,
        "run100",
        "repo__task-2",
        timestamp="20260602T000000",
        plan="later",
        resolved=True,
    )
    _write_pct_result(
        root,
        "run20",
        "repo__task-2",
        timestamp="20260601T000000",
        plan="first",
        resolved=False,
    )
    _write_pct_result(
        root,
        "run20",
        "repo__task-1",
        timestamp="20260601T010000",
        plan="only",
        resolved=True,
    )
    mock_loader_cls.return_value.load_instance.side_effect = lambda instance_id: {
        "problem_statement": f"issue for {instance_id}"
    }
    config = Config(
        system=SystemConfig(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            instances=["repo__task-1", "repo__task-2"],
        )
    )
    output = tmp_path / "cases.jsonl"

    manifest = build_pct_checker_input(
        config=config, pct_root=root, output_path=output
    )
    cases = [json.loads(line) for line in output.read_text().splitlines()]

    assert [case["instance_id"] for case in cases] == [
        "repo__task-1",
        "repo__task-2",
    ]
    assert cases[1]["plan"] == "first"
    assert cases[1]["resolved"] is False
    assert cases[1]["issue_description"] == "issue for repo__task-2"
    assert manifest["selected_instances"] == 2
    first_bytes = output.read_bytes()
    build_pct_checker_input(config=config, pct_root=root, output_path=output)
    assert output.read_bytes() == first_bytes


@patch("evaluate_checker.InstanceLoader")
def test_snapshot_publish_is_immutable_and_indexed(mock_loader_cls, tmp_path):
    pct_root = tmp_path / "pct"
    _write_pct_result(
        pct_root,
        "run1",
        "repo__task-1",
        timestamp="20260601T010000",
        plan="first",
        resolved=False,
    )
    mock_loader_cls.return_value.load_instance.return_value = {
        "problem_statement": "issue"
    }
    config = Config(
        system=SystemConfig(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            instances=["repo__task-1"],
        )
    )
    snapshot_root = tmp_path / "snapshots"
    created_at = datetime(2026, 6, 9, tzinfo=timezone.utc)

    first = publish_pct_checker_snapshot(
        config=config,
        pct_root=pct_root,
        snapshot_root=snapshot_root,
        created_at=created_at,
    )
    first_dir = snapshot_root / first["snapshot_id"]
    first_cases = (first_dir / "cases.jsonl").read_bytes()

    repeated = publish_pct_checker_snapshot(
        config=config,
        pct_root=pct_root,
        snapshot_root=snapshot_root,
        created_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
    )
    assert repeated["snapshot_id"] == first["snapshot_id"]
    assert (first_dir / "cases.jsonl").read_bytes() == first_cases

    _write_pct_result(
        pct_root,
        "run2",
        "repo__task-2",
        timestamp="20260602T010000",
        plan="second",
        resolved=True,
    )
    config = replace(
        config,
        system=replace(
            config.system,
            instances=["repo__task-1", "repo__task-2"],
        ),
    )
    mock_loader_cls.return_value.load_instance.side_effect = lambda _: {
        "problem_statement": "issue"
    }
    second = publish_pct_checker_snapshot(
        config=config,
        pct_root=pct_root,
        snapshot_root=snapshot_root,
        created_at=created_at,
    )

    assert second["snapshot_id"] != first["snapshot_id"]
    assert (first_dir / "cases.jsonl").read_bytes() == first_cases
    index = json.loads((snapshot_root / "index.json").read_text())
    assert len(index["snapshots"]) == 2
    assert index["latest_snapshot_id"] == second["snapshot_id"]
    assert index["latest_cases_path"] == second["cases_path"]


@patch("evaluate_checker.check_agent.run")
@patch("evaluate_checker.DockerEnvWrapper")
def test_checker_case_only_calls_existing_checker(
    mock_docker_cls, mock_check, tmp_path
):
    mock_check.return_value = (
        {"passed": True, "violations": [], "overall_assessment": "ok"},
        [{"role": "assistant", "content": "done"}],
    )
    loader = MagicMock()
    loader.load_instance.return_value = {
        "instance_id": "repo__task-1",
        "dataset_type": "polybench",
        "image_name": "image:latest",
    }
    config = Config(
        system=SystemConfig(dataset_type="polybench"),
        checker=CheckerConfig(enabled=True),
    )
    case = {
        "instance_id": "repo__task-1",
        "issue_description": "issue",
        "plan": "saved plan",
        "resolved": True,
    }

    result = _run_checker_case(case, config, "rules", tmp_path, loader)

    mock_check.assert_called_once_with(
        config, "saved plan", "issue", "rules", mock_docker_cls.return_value
    )
    mock_docker_cls.return_value.stop.assert_called_once()
    assert result["check_result"]["passed"] is True
    assert result["test_results"]["resolved"] is True


@patch("evaluate_checker.InstanceLoader")
@patch("evaluate_checker.format_rules_for_prompt", return_value="rules")
@patch("evaluate_checker.load_aggregated_rules", return_value={})
@patch("evaluate_checker._run_checker_case")
def test_checker_errors_are_excluded_from_metrics(
    mock_run_case, mock_load_rules, mock_format, mock_loader_cls, tmp_path
):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "instance_id": "good",
                "issue_description": "i",
                "plan": "p",
                "resolved": True,
            },
            {
                "instance_id": "bad",
                "issue_description": "i",
                "plan": "p",
                "resolved": False,
            },
        ],
    )
    mock_run_case.side_effect = [
        {
            "instance_id": "good",
            "check_result": {"passed": True, "violations": []},
            "test_results": {"resolved": True},
        },
        RuntimeError("checker failed"),
    ]

    results, errors = run_checker_only(
        config=Config(checker=CheckerConfig(enabled=True)),
        input_path=cases_path,
        output_dir=tmp_path / "out",
    )

    metrics = _compute_metrics(results)
    assert metrics["total"] == 1
    assert metrics["tp"] == 1
    assert len(errors) == 1
    saved_metrics = json.loads(
        (tmp_path / "out" / "metrics.json").read_text()
    )
    assert saved_metrics["checker_errors"] == 1
    assert saved_metrics["tn"] == 0


def test_compute_metrics_rejects_implicit_missing_values():
    with pytest.raises(KeyError):
        _compute_metrics([{"check_result": {}, "test_results": {}}])


@patch("evaluate_checker.InstanceLoader")
@patch("evaluate_checker.load_aggregated_rules")
@patch("evaluate_checker._run_checker_case")
def test_checker_only_resumes_matching_prediction(
    mock_run_case, mock_load_rules, mock_loader_cls, tmp_path
):
    cases_path = tmp_path / "cases.jsonl"
    _write_jsonl(
        cases_path,
        [
            {
                "instance_id": "repo__task-1",
                "issue_description": "issue",
                "plan": "plan",
                "plan_sha256": "plan-hash",
                "resolved": False,
            }
        ],
    )
    output = tmp_path / "out"
    prediction = {
        "instance_id": "repo__task-1",
        "check_result": {"passed": False, "violations": []},
        "test_results": {"resolved": False},
        "source": {"plan_sha256": "plan-hash"},
    }
    prediction_path = (
        output / "instances" / "repo__task-1" / "prediction.json"
    )
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text(json.dumps(prediction), encoding="utf-8")

    results, errors = run_checker_only(
        config=Config(checker=CheckerConfig(enabled=True)),
        input_path=cases_path,
        output_dir=output,
        rules_text_override="",
    )

    assert results == [prediction]
    assert errors == []
    mock_run_case.assert_not_called()
    mock_load_rules.assert_not_called()


@patch("evaluate_checker.InstanceLoader")
@patch("evaluate_checker._run_checker_case")
def test_checker_only_reruns_mismatched_plan_cache(
    mock_run_case, mock_loader_cls, tmp_path
):
    cases_path = tmp_path / "cases.jsonl"
    case = {
        "instance_id": "repo__task-1",
        "issue_description": "issue",
        "plan": "new plan",
        "plan_sha256": "new-hash",
        "resolved": True,
    }
    _write_jsonl(cases_path, [case])
    output = tmp_path / "out"
    prediction_path = (
        output / "instances" / "repo__task-1" / "prediction.json"
    )
    prediction_path.parent.mkdir(parents=True)
    prediction_path.write_text(
        json.dumps(
            {
                "instance_id": "repo__task-1",
                "check_result": {"passed": False},
                "test_results": {"resolved": True},
                "source": {"plan_sha256": "old-hash"},
            }
        ),
        encoding="utf-8",
    )
    replacement = {
        "instance_id": "repo__task-1",
        "check_result": {"passed": True, "violations": []},
        "test_results": {"resolved": True},
        "source": {"plan_sha256": "new-hash"},
    }
    mock_run_case.return_value = replacement

    results, _ = run_checker_only(
        config=Config(checker=CheckerConfig(enabled=True)),
        input_path=cases_path,
        output_dir=output,
        rules_text_override="",
    )

    assert results == [replacement]
    mock_run_case.assert_called_once()


@patch("evaluate_checker.configure_docker_capacity")
@patch("evaluate_checker.InstanceLoader")
@patch("evaluate_checker._run_checker_case")
def test_checker_only_parallel_preserves_input_order_and_cleans_once(
    mock_run_case, mock_loader_cls, mock_configure_window, tmp_path
):
    cases_path = tmp_path / "cases.jsonl"
    cases = [
        {
            "instance_id": f"repo__task-{index}",
            "issue_description": "issue",
            "plan": "plan",
            "plan_sha256": f"hash-{index}",
            "resolved": index % 2 == 0,
        }
        for index in range(4)
    ]
    _write_jsonl(cases_path, cases)
    active = 0
    peak = 0
    lock = threading.Lock()

    def run_case(
        case, config, rules_text, output_dir, loader, docker_window
    ):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return {
            "instance_id": case["instance_id"],
            "check_result": {"passed": True, "violations": []},
            "test_results": {"resolved": case["resolved"]},
            "source": {"plan_sha256": case["plan_sha256"]},
        }

    mock_run_case.side_effect = run_case
    results, errors = run_checker_only(
        config=Config(checker=CheckerConfig(enabled=True)),
        input_path=cases_path,
        output_dir=tmp_path / "out",
        rules_text_override="",
        max_workers=3,
    )

    assert [result["instance_id"] for result in results] == [
        case["instance_id"] for case in cases
    ]
    assert errors == []
    assert peak == 3
    mock_configure_window.assert_called_once_with(
        Config(checker=CheckerConfig(enabled=True)).docker,
        max_concurrent=3,
    )
    assert all(
        call.args[-1] is mock_configure_window.return_value
        for call in mock_run_case.call_args_list
    )
    mock_loader_cls.return_value.load_instance.assert_called_once_with(
        "repo__task-0"
    )
