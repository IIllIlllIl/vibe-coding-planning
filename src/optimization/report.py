"""Persist auditable GEPA results and candidate metrics."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from src.optimization.metrics import classification_metrics
from src.optimization.models import GEPACase


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def _phase_summary(records: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    selected = [
        record
        for record in records
        if record.get("event") == "model_call" and record.get("phase") == phase
    ]
    durations = [float(record.get("duration_seconds", 0.0)) for record in selected]
    models = Counter(
        str(record["model"])
        for record in selected
        if record.get("model")
    )
    provider_models = Counter(
        str(record["provider_model"])
        for record in selected
        if record.get("provider_model")
    )
    return {
        "calls": len(selected),
        "successful_calls": sum(bool(record.get("success")) for record in selected),
        "models": dict(sorted(models.items())),
        "provider_models": dict(sorted(provider_models.items())),
        "duration_seconds_total": sum(durations),
        "duration_seconds_mean": statistics.mean(durations) if durations else 0.0,
        "duration_seconds_p50": _percentile(durations, 0.5),
        "duration_seconds_p95": _percentile(durations, 0.95),
        "prompt_tokens": sum(int(record.get("prompt_tokens", 0)) for record in selected),
        "completion_tokens": sum(
            int(record.get("completion_tokens", 0)) for record in selected
        ),
        "total_tokens": sum(int(record.get("total_tokens", 0)) for record in selected),
        "reported_cost_usd": sum(
            float(record.get("reported_cost_usd", 0.0)) for record in selected
        ),
    }


def write_cost_report(
    run_dir: Path,
    *,
    observed_metric_calls: int,
    projection_metric_calls: int,
    parallel: int,
    run_status: str = "completed",
    successful_proposals: int = 0,
    required_proposals: int = 0,
    reflection_failures: int = 0,
) -> None:
    usage_path = run_dir / "usage.jsonl"
    records = (
        [
            json.loads(line)
            for line in usage_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if usage_path.is_file()
        else []
    )
    checker = _phase_summary(records, "checker")
    reflection = _phase_summary(records, "reflection")
    combined_models = Counter(checker["models"])
    combined_models.update(reflection["models"])
    combined_provider_models = Counter(checker["provider_models"])
    combined_provider_models.update(reflection["provider_models"])
    scale = projection_metric_calls / max(1, observed_metric_calls)
    limitations = []
    if run_status == "failed":
        limitations.append("run did not complete successfully")
    if successful_proposals < required_proposals:
        limitations.append(
            "successful Reflection proposals did not meet the configured minimum"
        )
    if reflection["calls"] == 0:
        limitations.append("no successful Reflection API calls were observed")
    if reflection_failures:
        limitations.append(
            f"{reflection_failures} Reflection proposal attempt(s) failed"
        )
    token_time_estimate_valid = run_status != "failed" and (
        successful_proposals >= required_proposals
    )
    report = {
        "run_quality": {
            "status": run_status,
            "successful_proposals": successful_proposals,
            "required_proposals": required_proposals,
            "reflection_failures": reflection_failures,
            "reflection_observed": reflection["calls"] > 0,
            "token_time_estimate_valid": token_time_estimate_valid,
            "limitations": limitations,
        },
        "checker": checker,
        "reflection": reflection,
        "combined": {
            "calls": checker["calls"] + reflection["calls"],
            "models": dict(sorted(combined_models.items())),
            "provider_models": dict(sorted(combined_provider_models.items())),
            "total_tokens": checker["total_tokens"] + reflection["total_tokens"],
            "reported_cost_usd": (
                checker["reported_cost_usd"] + reflection["reported_cost_usd"]
            ),
        },
        "observed": {
            "metric_calls": observed_metric_calls,
            "model_api_calls_per_metric_call": (
                (checker["calls"] + reflection["calls"])
                / max(1, observed_metric_calls)
            ),
            "tokens_per_metric_call": (
                (checker["total_tokens"] + reflection["total_tokens"])
                / max(1, observed_metric_calls)
            ),
            "cost_usd_per_metric_call": (
                (
                    checker["reported_cost_usd"]
                    + reflection["reported_cost_usd"]
                )
                / max(1, observed_metric_calls)
            ),
        },
        "full_run_linear_estimate": {
            "token_time_valid": token_time_estimate_valid,
            "target_metric_calls": projection_metric_calls,
            "estimated_checker_api_calls": checker["calls"] * scale,
            "estimated_reflection_api_calls": reflection["calls"] * scale,
            "estimated_total_tokens": (
                (checker["total_tokens"] + reflection["total_tokens"]) * scale
            ),
            "estimated_reported_cost_usd": (
                (
                    checker["reported_cost_usd"]
                    + reflection["reported_cost_usd"]
                )
                * scale
            ),
            "estimated_serial_model_seconds": (
                (
                    checker["duration_seconds_total"]
                    + reflection["duration_seconds_total"]
                )
                * scale
            ),
            "estimated_wall_seconds_with_parallelism": (
                checker["duration_seconds_total"] * scale / max(1, parallel)
                + reflection["duration_seconds_total"] * scale
            ),
            "method": "linear extrapolation from observed pilot API calls",
            "budget_semantics": (
                "GEPA checks max_metric_calls at iteration boundaries; "
                "observed calls may exceed the configured soft limit"
            ),
        },
    }
    (run_dir / "cost_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_report(result: Any, validation: list[GEPACase], run_dir: Path) -> None:
    evaluation_records = []
    evaluation_path = run_dir / "evaluations.jsonl"
    if evaluation_path.is_file():
        evaluation_records = [
            json.loads(line)
            for line in evaluation_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    candidates = []
    for index, candidate in enumerate(result.candidates):
        candidate_hash = _hash(candidate["rules"])
        scores = result.val_subscores[index]
        labels = []
        predictions = []
        predictions_by_instance = {}
        for val_id, score in scores.items():
            case = (
                validation[val_id]
                if isinstance(val_id, int)
                else next(
                    item for item in validation if item.instance_id == val_id
                )
            )
            labels.append(case.resolved)
            predictions.append(case.resolved if score == 1.0 else not case.resolved)
        for record in evaluation_records:
            if (
                record["candidate_sha256"] == candidate_hash
                and record["split"] == "validation"
            ):
                predictions_by_instance[record["instance_id"]] = record
        candidates.append(
            {
                "candidate_idx": index,
                "rules_sha256": candidate_hash,
                "validation_score": result.val_aggregate_scores[index],
                "metrics": classification_metrics(labels, predictions),
                "parents": result.parents[index],
                "validation_predictions": list(
                    predictions_by_instance.values()
                ),
            }
        )
    (run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=list)
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "candidate_metrics.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    best = result.best_candidate["rules"]
    (run_dir / "best_rules.txt").write_text(best + "\n", encoding="utf-8")
    (run_dir / "candidate_tree.html").write_text(
        result.candidate_tree_html(),
        encoding="utf-8",
    )
