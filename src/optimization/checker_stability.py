"""Repeat an Offline validation set to measure Checker decision stability."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.config import OptimizationConfig
from src.optimization.dataset import load_snapshot
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerResult,
    GEPACase,
)
from src.optimization.offline_hpc_executor import HPCSlurmOfflineCheckerExecutor


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_repetitions(
    cases: Sequence[GEPACase],
    results_by_repeat: Sequence[Sequence[CheckerResult | CheckerIncompleteOutput]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not results_by_repeat:
        raise ValueError("at least one Checker repetition is required")
    repetitions = len(results_by_repeat)
    if any(len(results) != len(cases) for results in results_by_repeat):
        raise ValueError("every repetition must cover the complete case set")

    rows: list[dict[str, Any]] = []
    correct_counts: Counter[int] = Counter()
    accept_counts: Counter[int] = Counter()
    incomplete = 0
    for index, case in enumerate(cases):
        outputs = [results[index] for results in results_by_repeat]
        completed = [output for output in outputs if isinstance(output, CheckerOutput)]
        predictions = [output.predicted_resolved for output in completed]
        row: dict[str, Any] = {
            "instance_id": case.instance_id,
            "historical_resolved": case.resolved,
            "statuses": [
                "completed"
                if isinstance(output, CheckerOutput)
                else output.to_dict()["status"]
                for output in outputs
            ],
            "failure_kinds": [
                output.failure_kind
                if isinstance(output, CheckerIncompleteOutput)
                else (
                    "checker_agent_timeout"
                    if not isinstance(output, CheckerOutput)
                    else None
                )
                for output in outputs
            ],
            "predicted_resolved": [
                output.predicted_resolved if isinstance(output, CheckerOutput) else None
                for output in outputs
            ],
        }
        if len(completed) == repetitions:
            correct_count = sum(
                prediction == case.resolved for prediction in predictions
            )
            accept_count = sum(predictions)
            row.update(
                {
                    "correct_count": correct_count,
                    "accept_count": accept_count,
                    "prediction_stable": len(set(predictions)) == 1,
                }
            )
            correct_counts[correct_count] += 1
            accept_counts[accept_count] += 1
        else:
            row.update(
                {
                    "correct_count": None,
                    "accept_count": None,
                    "prediction_stable": None,
                }
            )
            incomplete += 1
        rows.append(row)

    completed_triples = len(cases) - incomplete
    stable = correct_counts[0] + correct_counts[repetitions]
    summary = {
        "schema_version": 1,
        "cases": len(cases),
        "repetitions": repetitions,
        "completed_repetition_sets": completed_triples,
        "incomplete_repetition_sets": incomplete,
        "correct_count_distribution": {
            f"{count}/{repetitions}": correct_counts[count]
            for count in range(repetitions, -1, -1)
        },
        "accept_count_distribution": {
            f"{count}/{repetitions}": accept_counts[count]
            for count in range(repetitions, -1, -1)
        },
        "stable_prediction_count": stable,
        "unstable_prediction_count": completed_triples - stable,
        "stable_prediction_rate": (
            stable / completed_triples if completed_triples else None
        ),
    }
    return rows, summary


def run_checker_stability(
    config: OptimizationConfig,
    *,
    repetitions: int,
    config_path: Path,
) -> dict[str, Any] | None:
    if config.execution.backend != "hpc_slurm":
        raise ValueError("Checker stability diagnostic currently requires hpc_slurm")
    if repetitions < 2:
        raise ValueError("Checker stability diagnostic requires at least 2 repetitions")

    config.run_dir.mkdir(parents=True, exist_ok=True)
    status_path = config.run_dir / "controller_status.json"
    guideline = config.initial_rules_path.read_text(encoding="utf-8").strip()
    _, validation = load_snapshot(config.dataset_snapshot)
    manifest = {
        "schema_version": 1,
        "mode": "offline_checker_stability",
        "split": "validation",
        "instances": len(validation),
        "repetitions": repetitions,
        "guideline_sha256": text_sha256(guideline),
        "config_sha256": _sha256(config_path),
        "contains_reflection": False,
    }
    manifest_path = config.run_dir / "stability_manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise ValueError("Checker stability manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)

    executor = HPCSlurmOfflineCheckerExecutor(config)
    results_by_repeat: list[list[CheckerResult | CheckerIncompleteOutput]] = []
    try:
        for repeat_index in range(1, repetitions + 1):
            tag = f"repeat_{repeat_index:02d}"
            atomic_json(
                status_path,
                {
                    "schema_version": 1,
                    "status": "running",
                    "mode": "offline_checker_stability",
                    "current_repeat": repeat_index,
                    "repetitions": repetitions,
                },
            )
            results_by_repeat.append(
                executor.evaluate(
                    validation,
                    guideline,
                    capture_traces=True,
                    evaluation_tag=tag,
                    allow_incomplete=True,
                )
            )
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "status": "yielded",
                "mode": "offline_checker_stability",
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
                "status": "failed",
                "mode": "offline_checker_stability",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    rows, summary = summarize_repetitions(validation, results_by_repeat)
    rows_path = config.run_dir / "case_stability.jsonl"
    temporary = rows_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(rows_path)
    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(config.run_dir / "stability_summary.json", summary)
    result = {
        "schema_version": 1,
        "mode": "offline_checker_stability",
        "run_status": "completed",
        "summary": summary,
    }
    atomic_json(config.run_dir / "result.json", result)
    atomic_json(
        status_path,
        {
            "schema_version": 1,
            "status": "completed",
            "mode": "offline_checker_stability",
        },
    )
    return result
