"""Resume-safe controller for paired PolyBench PCCE evaluation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pcce.config import PolyBenchPCCEConfig
from src.polybench_pcce.dataset import load_pcce_cases
from src.polybench_pcce.hpc_executor import (
    PolyBenchPCCEHPCExecutor,
    pcce_semantic_sha256,
)
from src.polybench_pcce.models import CEAssignment, PCCECase, PCReviewAssignment
from src.polybench_pce.controller import _git_head
from src.polybench_pce.dataset import file_sha256


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _class_metrics(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall)
            if precision is not None
            and recall is not None
            and precision + recall
            else None
        ),
    }


def _checker_only_result(
    config: PolyBenchPCCEConfig,
    cases: list[PCCECase],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist first-review decisions without invoking revision or CE phases."""
    by_id = {str(item["instance_id"]): item for item in reviews}
    rows: list[dict[str, Any]] = []
    tp = tn = fp = fn = incomplete = 0
    for case in cases:
        review = by_id.get(case.instance_id)
        checker = review.get("checker_output") if isinstance(review, dict) else None
        proceed = checker.get("should_proceed") if isinstance(checker, dict) else None
        if not isinstance(proceed, bool):
            incomplete += 1
            correct = False
        else:
            correct = proceed == case.baseline_resolved
            if proceed and case.baseline_resolved:
                tn += 1
            elif proceed:
                fn += 1
            elif case.baseline_resolved:
                fp += 1
            else:
                tp += 1
        rows.append(
            {
                "instance_id": case.instance_id,
                "baseline_pce_resolved": case.baseline_resolved,
                "predicted_should_proceed": proceed,
                "correct": correct,
                "review": review,
            }
        )
    _write_jsonl(config.run_dir / "pc_outcomes.jsonl", rows)
    completed = len(cases) - incomplete
    bad_recall = _ratio(tp, tp + fn)
    good_recall = _ratio(tn, tn + fp)
    mcc_denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    summary = {
        "schema_version": 1,
        "mode": "polybench_pc_checker_only",
        "status": "completed" if not incomplete else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "instances": len(cases),
        "completed": completed,
        "operationally_incomplete": incomplete,
        "accuracy": _ratio(tp + tn, len(cases)),
        "completed_only_accuracy": _ratio(tp + tn, completed),
        "balanced_accuracy": (
            (bad_recall + good_recall) / 2
            if bad_recall is not None and good_recall is not None
            else None
        ),
        "mcc": (
            (tp * tn - fp * fn) / mcc_denominator if mcc_denominator else None
        ),
        "confusion_rejection_positive": {
            "reject_bad_tp": tp,
            "reject_good_fp": fp,
            "accept_good_tn": tn,
            "accept_bad_fn": fn,
        },
        "rejection_precision": _ratio(tp, tp + fp),
        "bad_plan_recall": bad_recall,
        "accept_precision": _ratio(tn, tn + fn),
        "good_plan_recall": good_recall,
        "rejected_as_positive": _class_metrics(tp, fp, fn),
        "accepted_as_positive": _class_metrics(tn, fn, fp),
    }
    atomic_json(config.run_dir / "result.json", summary)
    atomic_json(config.run_dir / "controller_status.json", summary)
    return summary


def _review_artifact(
    config: PolyBenchPCCEConfig, review_index: int, instance_id: str
) -> Path:
    return (
        config.run_dir
        / "reviews"
        / f"review_{review_index:02d}"
        / f"{instance_id}.json"
    )


def _review_assignments(
    cases: list[PCCECase],
    prior: list[dict[str, Any]] | None,
    review_index: int,
) -> list[PCReviewAssignment]:
    if review_index == 1:
        return [
            PCReviewAssignment(case, 1, 0, case.baseline_plan, "") for case in cases
        ]
    if prior is None:
        raise ValueError("later PCCE review requires the preceding review results")
    case_by_id = {case.instance_id: case for case in cases}
    assignments: list[PCReviewAssignment] = []
    for result in prior:
        checker = result.get("checker_output")
        if result.get("status") != "completed" or not isinstance(checker, dict):
            continue
        if checker.get("should_proceed") is False:
            assignments.append(
                PCReviewAssignment(
                    case=case_by_id[str(result["instance_id"])],
                    review_index=review_index,
                    rejection_count=int(result["rejection_count_after_review"]),
                    input_plan=str(result["plan"]),
                    previous_feedback=str(checker.get("revision_feedback", "")),
                )
            )
    return assignments


def run_polybench_pcce(config: PolyBenchPCCEConfig) -> dict[str, Any] | None:
    cases, identities = load_pcce_cases(config)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    guideline = config.guideline_path.read_text(encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pcce",
        "contains_gepa": False,
        "contains_reflection": False,
        "project_git_head": _git_head(),
        "config_sha256": file_sha256(config.config_path),
        "pcce_semantic_sha256": pcce_semantic_sha256(config),
        "source_manifest_sha256": file_sha256(config.source_snapshot / "manifest.json"),
        "image_manifest_sha256": file_sha256(config.image_manifest),
        "validation_manifest_sha256": identities["validation_manifest_sha256"],
        "validation_file_sha256": identities["validation_file_sha256"],
        "pce_outcomes_sha256": identities["pce_outcomes_sha256"],
        "selection_manifest_sha256": (
            file_sha256(config.selection_manifest)
            if config.selection_manifest is not None
            else None
        ),
        "guideline_label": config.guideline_label,
        "guideline_sha256": text_sha256(guideline),
        "instance_ids": [case.instance_id for case in cases],
        "paired_first_plan": True,
        "execution_mode": config.execution_mode,
        "max_review_rejections": config.max_review_rejections,
        "workflow_task_attempts": config.hpc.max_task_attempts,
        "workflow_attempts_consume_review_budget": False,
        "historical_pce_code_evaluate_reused": False,
        "planner_code_evaluate_enabled": config.execution_mode == "full_pcce",
        "repository_baseline": {
            "declared_revision": "dataset_base_commit",
            "restore": "git reset --hard <base_commit> && git clean -fd",
            "verified_agent_phases": (
                ["checker"]
                if config.execution_mode == "checker_only"
                else ["checker", "plan_revision", "code"]
            ),
            "evaluate_verified_by_pce_runner": (
                config.execution_mode == "full_pcce"
            ),
        },
    }
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("PolyBench PCCE run manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)
    status_path = config.run_dir / "controller_status.json"
    atomic_json(
        status_path,
        {"schema_version": 1, "mode": "polybench_pcce", "status": "running"},
    )
    executor = PolyBenchPCCEHPCExecutor(config)

    review_results: dict[int, list[dict[str, Any]]] = {}
    try:
        prior: list[dict[str, Any]] | None = None
        for review_index in range(1, config.max_review_rejections + 1):
            assignments = _review_assignments(cases, prior, review_index)
            if not assignments:
                break
            wave_path = config.run_dir / "reviews" / f"review_{review_index:02d}.jsonl"
            if wave_path.is_file():
                results = _read_jsonl(wave_path)
                if [item["instance_id"] for item in results] != [
                    item.case.instance_id for item in assignments
                ]:
                    raise ValueError("PCCE persisted review wave identity mismatch")
            else:
                results = executor.run_pc(assignments)
                _write_jsonl(wave_path, results)
            # Recreate deterministic per-case references even if a prior
            # controller stopped after the atomic wave write.
            for result in results:
                artifact = _review_artifact(
                    config, review_index, str(result["instance_id"])
                )
                if artifact.is_file():
                    if json.loads(artifact.read_text(encoding="utf-8")) != result:
                        raise ValueError(
                            "PCCE review artifact differs from its wave result"
                        )
                else:
                    atomic_json(artifact, result)
            review_results[review_index] = results
            prior = results

        latest: dict[str, dict[str, Any]] = {}
        accepted: dict[str, tuple[int, dict[str, Any]]] = {}
        for review_index, results in review_results.items():
            for result in results:
                instance_id = str(result["instance_id"])
                latest[instance_id] = result
                checker = result.get("checker_output")
                if (
                    result.get("status") == "completed"
                    and isinstance(checker, dict)
                    and checker.get("should_proceed") is True
                ):
                    accepted[instance_id] = (review_index, result)

        if config.execution_mode == "checker_only":
            return _checker_only_result(config, cases, review_results.get(1, []))

        ce_assignments = [
            CEAssignment(
                case,
                _review_artifact(
                    config, accepted[case.instance_id][0], case.instance_id
                ),
                str(accepted[case.instance_id][1]["plan"]),
            )
            for case in cases
            if case.instance_id in accepted
        ]
        ce_path = config.run_dir / "ce_outcomes.jsonl"
        if ce_assignments:
            if ce_path.is_file():
                ce_results = _read_jsonl(ce_path)
                if [item["instance_id"] for item in ce_results] != [
                    item.case.instance_id for item in ce_assignments
                ]:
                    raise ValueError("PCCE persisted CE identity mismatch")
            else:
                ce_results = executor.run_ce(ce_assignments)
                _write_jsonl(ce_path, ce_results)
        else:
            ce_results = []
            if not ce_path.exists():
                _write_jsonl(ce_path, [])
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "polybench_pcce",
                "status": "yielded",
                "reason": exc.reason,
                "batch_dir": exc.batch_dir,
                "worker_job_id": exc.job_id,
            },
        )
        return None
    except Exception as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "polybench_pcce",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    ce_by_id = {str(result["instance_id"]): result for result in ce_results}
    final_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for case in cases:
        instance_id = case.instance_id
        if instance_id in accepted:
            review_index, review = accepted[instance_id]
            ce = ce_by_id.get(instance_id)
            if ce is not None and ce.get("status") == "completed":
                evaluator = ce.get("evaluator_result")
                resolved = (
                    evaluator.get("evaluator_resolved")
                    if isinstance(evaluator, dict)
                    else None
                )
                outcome = (
                    "resolved"
                    if resolved is True
                    else "unresolved"
                    if resolved is False
                    else "unknown"
                )
                counts[outcome] += 1
                method_status = "completed"
            else:
                resolved = None
                method_status = "operational_incomplete"
                counts[method_status] += 1
            final_rows.append(
                {
                    "instance_id": instance_id,
                    "method_status": method_status,
                    "baseline_pce_resolved": case.baseline_resolved,
                    "accepted_review_index": review_index,
                    "review_rejections": int(review["rejection_count_before_review"]),
                    "pcce_resolved": resolved,
                    "accepted_plan_sha256": text_sha256(str(review["plan"])),
                    "ce_output": ce,
                }
            )
            continue
        terminal = latest.get(instance_id)
        if terminal is None or terminal.get("status") != "completed":
            method_status = "operational_incomplete"
            rejection_count = None
        else:
            method_status = "checker_rejected_after_3_reviews"
            rejection_count = int(terminal["rejection_count_after_review"])
        counts[method_status] += 1
        final_rows.append(
            {
                "instance_id": instance_id,
                "method_status": method_status,
                "baseline_pce_resolved": case.baseline_resolved,
                "review_rejections": rejection_count,
                "pcce_resolved": False
                if method_status == "checker_rejected_after_3_reviews"
                else None,
                "terminal_review": terminal,
            }
        )
    _write_jsonl(config.run_dir / "pcce_outcomes.jsonl", final_rows)
    summary = {
        "schema_version": 1,
        "mode": "polybench_pcce",
        "status": "completed"
        if not counts["operational_incomplete"]
        else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "instances": len(cases),
        "baseline_pce_resolved": sum(case.baseline_resolved for case in cases),
        "method_outcomes": dict(sorted(counts.items())),
        "first_review_passes": sum(
            item.get("status") == "completed"
            and (item.get("checker_output") or {}).get("should_proceed") is True
            for item in review_results.get(1, [])
        ),
        "passed_after_revision": sum(index > 1 for index, _ in accepted.values()),
        "rejected_after_three": counts["checker_rejected_after_3_reviews"],
    }
    atomic_json(config.run_dir / "result.json", summary)
    atomic_json(status_path, summary)
    return summary
