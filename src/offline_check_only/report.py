"""Deterministic reports for fixed-guideline Checker predictions."""

from __future__ import annotations

import math
from typing import Any, Sequence

from src.offline_check_only.dataset import CheckOnlyCase
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerResult,
    CheckerTimeoutOutput,
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _class_metrics(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def summarize(
    cases: Sequence[CheckOnlyCase],
    results: Sequence[CheckerResult | CheckerIncompleteOutput],
) -> dict[str, Any]:
    if len(cases) != len(results):
        raise ValueError("case and result counts differ")
    completed = [
        (case, result)
        for case, result in zip(cases, results, strict=True)
        if isinstance(result, CheckerOutput)
    ]
    tp = sum(case.resolved and result.predicted_resolved for case, result in completed)
    tn = sum(not case.resolved and not result.predicted_resolved for case, result in completed)
    fp = sum(not case.resolved and result.predicted_resolved for case, result in completed)
    fn = sum(case.resolved and not result.predicted_resolved for case, result in completed)
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    resolved_recall = _ratio(tp, tp + fn)
    unresolved_recall = _ratio(tn, tn + fp)
    return {
        "cases": len(cases),
        "completed": len(completed),
        "timeouts": sum(isinstance(result, CheckerTimeoutOutput) for result in results),
        "operationally_incomplete": sum(
            isinstance(result, CheckerIncompleteOutput) for result in results
        ),
        "correct": tp + tn,
        "accuracy": _ratio(tp + tn, len(cases)),
        "completed_only_accuracy": _ratio(tp + tn, len(completed)),
        "balanced_accuracy": (
            (resolved_recall + unresolved_recall) / 2
            if resolved_recall is not None and unresolved_recall is not None
            else None
        ),
        "mcc": (tp * tn - fp * fn) / denominator if denominator else None,
        "pass_rate": _ratio(tp + fp, len(completed)),
        "rejection_rate": _ratio(tn + fn, len(completed)),
        "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
        "resolved_as_positive": _class_metrics(tp, fp, fn),
        "rejected_as_positive": _class_metrics(tn, fn, fp),
    }


def report_views(
    cases: Sequence[CheckOnlyCase],
    results: Sequence[CheckerResult | CheckerIncompleteOutput],
) -> dict[str, dict[str, Any]]:
    selectors = {
        "raw": lambda case: True,
        "cleaned": lambda case: not case.excluded_from_cleaned,
        "bug_fix_cleaned": lambda case: (
            not case.excluded_from_cleaned and case.task_category == "Bug Fix"
        ),
        "non_bug_fix_cleaned": lambda case: (
            not case.excluded_from_cleaned and case.task_category != "Bug Fix"
        ),
        "train": lambda case: case.split == "train",
        "validation": lambda case: case.split == "validation",
    }
    views: dict[str, dict[str, Any]] = {}
    for name, include in selectors.items():
        selected = [
            (case, result)
            for case, result in zip(cases, results, strict=True)
            if include(case)
        ]
        views[name] = summarize(
            [item[0] for item in selected],
            [item[1] for item in selected],
        )
    return views
