"""Behavioral candidate metrics with acceptability terminology."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from src.optimization.behavioral_models import BehavioralGEPACase
from src.optimization.metrics import classification_metrics


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_behavioral_report(
    result: Any,
    validation: list[BehavioralGEPACase],
    run_dir: Path,
    *,
    primary_metric: str,
) -> None:
    path = run_dir / "behavioral_evaluations.jsonl"
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    candidates = []
    for index, candidate in enumerate(result.candidates):
        candidate_sha256 = _hash(candidate["rules"])
        by_id = {
            str(record["instance_id"]): record
            for record in records
            if record.get("candidate_sha256") == candidate_sha256
            and record.get("split") == "validation"
        }
        expected_ids = {case.instance_id for case in validation}
        if set(by_id) != expected_ids:
            raise ValueError(
                "Behavioral validation predictions do not match the frozen split"
            )
        ordered = [by_id[case.instance_id] for case in validation]
        completed = [
            (case.accepted, record["output"]["predicted_accept"])
            for case, record in zip(validation, ordered, strict=True)
            if isinstance(record.get("output", {}).get("predicted_accept"), bool)
        ]
        labels = [label for label, _ in completed]
        predictions = [prediction for _, prediction in completed]
        metrics = classification_metrics(labels, predictions)
        tn = int(metrics["tn"])
        fn = int(metrics["fn"])
        fp = int(metrics["fp"])
        metrics.update(
            {
                "accept_precision": metrics["precision"],
                "accept_recall": metrics["recall"],
                "do_not_accept_precision": tn / (tn + fn) if tn + fn else 0.0,
                "do_not_accept_recall": tn / (tn + fp) if tn + fp else 0.0,
            }
        )
        validation_score = float(result.val_aggregate_scores[index])
        recomputed = sum(float(record["score"]) for record in ordered) / len(ordered)
        if not math.isclose(validation_score, recomputed, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Behavioral candidate validation score mismatch")
        candidates.append(
            {
                "candidate_idx": index,
                "guideline_sha256": candidate_sha256,
                "primary_metric": primary_metric,
                "validation_score": validation_score,
                "metrics": metrics,
                "metrics_scope": "completed_checker_predictions_only",
                "operationally_incomplete_count": len(validation) - len(completed),
                "parents": result.parents[index],
                "validation_predictions": ordered,
            }
        )
    (run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=list) + "\n",
        encoding="utf-8",
    )
    (run_dir / "behavioral_candidate_metrics.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "best_guideline.txt").write_text(
        result.best_candidate["rules"] + "\n", encoding="utf-8"
    )
    (run_dir / "candidate_tree.html").write_text(
        result.candidate_tree_html(), encoding="utf-8"
    )
