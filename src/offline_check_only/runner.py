"""Controller for frozen guideline evaluation without GEPA or Reflection."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.exceptions import ControllerYield
from src.offline_check_only.config import CheckOnlyConfig, file_sha256
from src.offline_check_only.dataset import CheckOnlyCase, load_validation_cases
from src.offline_check_only.executor import CheckerAssignment, CheckOnlyHPCExecutor
from src.offline_check_only.guidelines import load_guidelines
from src.offline_check_only.report import report_views
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.models import CheckerOutput, CheckerResult
from src.optimization.offline_hpc_executor import offline_checker_semantic_sha256


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def _manifest(
    config: CheckOnlyConfig,
    dataset_manifest: dict[str, Any],
    guideline_manifest: dict[str, Any],
    cases: list[CheckOnlyCase],
    guidelines: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "offline_check_only",
        "contains_gepa": False,
        "contains_reflection": False,
        "check_only_config_sha256": file_sha256(config.config_path),
        "dataset": {
            "name": config.dataset.name,
            "type": config.dataset.type,
            "snapshot_id": dataset_manifest.get("snapshot_id"),
            "case_file": config.dataset.case_file,
            "case_file_sha256": file_sha256(
                config.dataset.snapshot / config.dataset.case_file
            ),
            "manifest_sha256": file_sha256(
                config.dataset.snapshot / "manifest.json"
            ),
            "instances": len(cases),
        },
        "guidelines": {
            "bundle_id": guideline_manifest.get("bundle_id"),
            "manifest_sha256": file_sha256(
                config.guideline_bundle / "manifest.json"
            ),
            "labels": list(config.guideline_labels),
            "sha256": {
                label: text_sha256(guidelines[label])
                for label in config.guideline_labels
            },
        },
        "checker_runtime_config": str(config.runtime_config_path),
        "checker_runtime_config_sha256": file_sha256(config.runtime_config_path),
        "checker_semantic_sha256": offline_checker_semantic_sha256(config.runtime),
        "attempt_policy": {
            "max_task_attempts": config.runtime.hpc.max_task_attempts,
            "same_as_offline_checker": True,
        },
        "task_count": len(cases) * len(guidelines),
    }


def _prediction_row(
    case: CheckOnlyCase,
    result: CheckerResult,
    label: str,
) -> dict[str, Any]:
    output = result.to_dict(include_trajectory=False)
    return {
        "instance_id": case.instance_id,
        "guideline_label": label,
        "historical_resolved": case.resolved,
        "task_category": case.task_category,
        "language": case.language,
        "excluded_from_cleaned": case.excluded_from_cleaned,
        "exclusion_reason": case.exclusion_reason,
        "checker_output": output,
        "correct": (
            result.predicted_resolved == case.resolved
            if isinstance(result, CheckerOutput)
            else False
        ),
    }


def run_check_only(config: CheckOnlyConfig) -> dict[str, Any] | None:
    if config.runtime.execution.backend != "hpc_slurm":
        raise ValueError("offline_check_only currently requires hpc_slurm")
    config.run_dir.mkdir(parents=True, exist_ok=True)
    status_path = config.run_dir / "controller_status.json"
    cases, dataset_manifest = load_validation_cases(config.dataset)
    guidelines, guideline_manifest = load_guidelines(
        config.guideline_bundle,
        config.guideline_labels,
    )
    manifest = _manifest(
        config,
        dataset_manifest,
        guideline_manifest,
        cases,
        guidelines,
    )
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("check-only manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)

    assignments = [
        CheckerAssignment(case=case, guideline_label=label, guideline=guidelines[label])
        for label in config.guideline_labels
        for case in cases
    ]
    atomic_json(
        status_path,
        {
            "schema_version": 1,
            "status": "running",
            "mode": "offline_check_only",
            "tasks": len(assignments),
        },
    )
    try:
        flat_results = CheckOnlyHPCExecutor(config).evaluate_assignments(assignments)
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "status": "yielded",
                "mode": "offline_check_only",
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
                "mode": "offline_check_only",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    by_label: dict[str, list[CheckerResult]] = {}
    offset = 0
    metrics: dict[str, Any] = {}
    for label in config.guideline_labels:
        results = flat_results[offset : offset + len(cases)]
        offset += len(cases)
        by_label[label] = results
        _write_jsonl(
            config.run_dir / "predictions" / f"{label}.jsonl",
            [
                _prediction_row(case, result, label)
                for case, result in zip(cases, results, strict=True)
            ],
        )
        metrics[label] = report_views(cases, results)
    atomic_json(config.run_dir / "metrics.json", metrics)

    reference = config.guideline_labels[0]
    differences = []
    for case_index, case in enumerate(cases):
        predictions = {
            label: (
                by_label[label][case_index].predicted_resolved
                if isinstance(by_label[label][case_index], CheckerOutput)
                else None
            )
            for label in config.guideline_labels
        }
        if len(set(predictions.values())) > 1:
            differences.append(
                {
                    "instance_id": case.instance_id,
                    "historical_resolved": case.resolved,
                    "reference_guideline": reference,
                    "predictions": predictions,
                }
            )
    _write_jsonl(config.run_dir / "differences.jsonl", differences)
    result = {
        "schema_version": 1,
        "mode": "offline_check_only",
        "run_status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "difference_cases": len(differences),
    }
    atomic_json(config.run_dir / "result.json", result)
    atomic_json(
        status_path,
        {"schema_version": 1, "status": "completed", "mode": "offline_check_only"},
    )
    return result
