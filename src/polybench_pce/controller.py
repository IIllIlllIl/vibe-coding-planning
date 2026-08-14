"""Resume-safe controller for raw PolyBench PCE evidence generation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from src.exceptions import ControllerYield
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pce.config import PolyBenchPCEConfig
from src.polybench_pce.dataset import (
    canonical_image_ref,
    file_sha256,
    load_polybench_pce_cases,
)
from src.polybench_pce.hpc_executor import (
    PolyBenchPCEHPCExecutor,
    execution_fingerprint,
    pce_semantic_sha256,
)


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


def _git_head() -> str | None:
    submitted_head = os.environ.get("VIBE_PROJECT_GIT_HEAD")
    if submitted_head is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", submitted_head):
            raise ValueError("VIBE_PROJECT_GIT_HEAD must be a full lowercase Git SHA")
        return submitted_head
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def run_polybench_pce(config: PolyBenchPCEConfig) -> dict[str, Any] | None:
    cases, dataset_manifest, image_manifest = load_polybench_pce_cases(
        config.dataset_snapshot,
        config.image_manifest,
    )
    config.run_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = execution_fingerprint(config, cases)
    available_ids = {case.instance_id for case in cases}
    source_rows = [
        json.loads(line)
        for line in (
            config.dataset_snapshot
            / str(dataset_manifest.get("instances_file", "instances.jsonl"))
        )
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    unavailable = [
        str(wrapper["source_row"]["instance_id"])
        for wrapper in source_rows
        if str(wrapper["source_row"]["instance_id"]) not in available_ids
    ]
    image_records = image_manifest.get("records", {})
    unavailable_evidence = []
    for instance_id in unavailable:
        image_ref = canonical_image_ref(instance_id)
        record = image_records.get(image_ref)
        unavailable_evidence.append(
            {
                "instance_id": instance_id,
                "requested_ref": image_ref,
                "image_record": dict(record) if isinstance(record, dict) else None,
            }
        )
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pce",
        "contains_gepa": False,
        "contains_reflection": False,
        "assigns_final_validation_labels": False,
        "config_sha256": file_sha256(config.config_path),
        "project_git_head": _git_head(),
        "pce_semantic_sha256": pce_semantic_sha256(config),
        "execution_fingerprint": fingerprint,
        "dataset_snapshot": str(config.dataset_snapshot),
        "dataset_manifest_sha256": file_sha256(
            config.dataset_snapshot / "manifest.json"
        ),
        "dataset_revision": dataset_manifest.get("revision"),
        "image_manifest": str(config.image_manifest),
        "image_manifest_sha256": file_sha256(config.image_manifest),
        "image_manifest_identity": image_manifest.get("manifest_id"),
        "source_instances": len(source_rows),
        "image_available_instances": len(cases),
        "image_unavailable_instances": unavailable,
        "image_unavailable_evidence": unavailable_evidence,
        "instance_ids": [case.instance_id for case in cases],
        "attempt_policy": {
            "total_attempts": config.hpc.max_task_attempts,
            "fresh_agent_from_first_incomplete_phase": True,
            "completed_phase_checkpoints_are_reused": True,
            "prior_attempt_failures_are_not_agent_input": True,
        },
        "phase_isolation": {
            "plan": "fresh_apptainer",
            "code": "fresh_apptainer",
            "evaluate": "fresh_apptainer",
        },
    }
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("PolyBench PCE run manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)

    status_path = config.run_dir / "controller_status.json"
    atomic_json(
        status_path,
        {
            "schema_version": 1,
            "mode": "polybench_pce",
            "status": "running",
            "execution_fingerprint": fingerprint,
            "tasks": len(cases),
        },
    )
    try:
        outcomes = PolyBenchPCEHPCExecutor(config).evaluate(cases)
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "polybench_pce",
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
                "mode": "polybench_pce",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    _write_jsonl(config.run_dir / "raw_pce_outcomes.jsonl", outcomes)
    completed = sum(item.get("status") == "completed" for item in outcomes)
    incomplete = len(outcomes) - completed
    task_outcomes = Counter()
    outcome_reasons = Counter()
    for item in outcomes:
        evaluator = item.get("evaluator_result")
        if isinstance(evaluator, dict):
            task_outcomes[str(evaluator.get("task_outcome", "unknown"))] += 1
            outcome_reasons[str(evaluator.get("outcome_reason", "unclassified"))] += 1
        else:
            task_outcomes["incomplete"] += 1
            last = item.get("last_worker_output")
            reason = (
                str(last.get("outcome_reason", "attempts_exhausted"))
                if isinstance(last, dict)
                else "attempts_exhausted"
            )
            outcome_reasons[reason] += 1
    summary = {
        "schema_version": 1,
        "mode": "polybench_pce",
        "status": "completed" if incomplete == 0 else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "instances": len(outcomes),
        "source_instances": len(source_rows),
        "image_unavailable_instances": len(unavailable),
        "completed_instances": completed,
        "incomplete_instances": incomplete,
        "task_outcomes": dict(sorted(task_outcomes.items())),
        "outcome_reasons": dict(sorted(outcome_reasons.items())),
        "final_validation_labels_assigned": 0,
    }
    atomic_json(config.run_dir / "result.json", summary)
    atomic_json(status_path, summary)
    return summary
