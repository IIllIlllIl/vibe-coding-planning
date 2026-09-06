"""Resume-safe controller for raw SWE-bench Pro PCE evidence."""

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
from src.swe_bench_pro_pce.config import SWEBenchProPCEConfig
from src.swe_bench_pro_pce.dataset import file_sha256, load_swe_bench_pro_pce_cases
from src.swe_bench_pro_pce.hpc_executor import SWEBenchProPCEHPCExecutor


def _git_head() -> str | None:
    submitted = os.environ.get("VIBE_PROJECT_GIT_HEAD")
    if submitted is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", submitted):
            raise ValueError("VIBE_PROJECT_GIT_HEAD must be a full lowercase Git SHA")
        return submitted
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def run_swe_bench_pro_pce(config: SWEBenchProPCEConfig) -> dict[str, Any] | None:
    cases, source, images = load_swe_bench_pro_pce_cases(config.dataset_snapshot, config.image_manifest)
    by_id = {case.instance_id: case for case in cases}
    missing = [instance_id for instance_id in config.instance_ids if instance_id not in by_id]
    if missing:
        raise ValueError("selected Pro instances lack audited images: " + ", ".join(missing))
    cases = [by_id[instance_id] for instance_id in config.instance_ids]
    executor = SWEBenchProPCEHPCExecutor(config)
    fingerprint = executor.execution_fingerprint(cases)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "mode": config.mode,
        "contains_gepa": False,
        "contains_reflection": False,
        "assigns_final_validation_labels": False,
        "config_sha256": file_sha256(config.config_path),
        "project_git_head": _git_head(),
        "pce_semantic_sha256": executor.semantic_sha256(),
        "execution_fingerprint": fingerprint,
        "dataset_manifest_sha256": file_sha256(config.dataset_snapshot / "manifest.json"),
        "dataset_revision": source["revision"],
        "official_evaluator_revision": source["official_evaluator_revision"],
        "image_manifest_sha256": file_sha256(config.image_manifest),
        "selection_manifest_sha256": file_sha256(config.selection_manifest),
        "execution_instances": len(cases),
        "instance_ids": [case.instance_id for case in cases],
        "attempt_policy": {
            "total_attempts": 3,
            "fresh_agent_from_first_incomplete_phase": True,
            "completed_phase_checkpoints_are_reused": True,
            "prior_attempt_failures_are_not_agent_input": True,
        },
        "phase_isolation": {
            "plan": "fresh_apptainer_containall_official_sif_workspace",
            "code": "fresh_apptainer_containall_official_sif_workspace",
            "evaluate": "fresh_apptainer_official_sif_workspace",
            "network_policy": "unchanged_from_current_swe_pce",
        },
        "repository_baseline": {
            "declared_revision": "dataset_base_commit",
            "restore": "none_after_official_image_build",
            "official_build_artifacts_are_preserved": True,
            "clean_worktree_required": False,
            "empty_staging_area_required": True,
            "verified_phases": ["plan", "code", "evaluate"],
            "agent_workspace_policy": images["agent_workspace_policy"],
            "history_contamination_policy": images["history_contamination_policy"],
            "latent_future_history_present": True,
        },
        "evaluation": {
            "repository_workdir": "/app",
            "gold_test_checkout_is_evaluator_only": True,
            "official_run_and_parser_assets_are_frozen": True,
            "slurm_exhaustion_outcome": "unknown",
        },
    }
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError("Pro PCE run manifest differs from existing run")
    if not manifest_path.exists():
        atomic_json(manifest_path, manifest)
    status_path = config.run_dir / "controller_status.json"
    atomic_json(status_path, {"schema_version": 1, "mode": config.mode, "status": "running", "tasks": len(cases), "execution_fingerprint": fingerprint})
    try:
        outcomes = executor.evaluate(cases)
    except ControllerYield as exc:
        atomic_json(status_path, {"schema_version": 1, "mode": config.mode, "status": "yielded", "reason": exc.reason, "batch_dir": exc.batch_dir, "worker_job_id": exc.job_id})
        return None
    except Exception as exc:
        atomic_json(status_path, {"schema_version": 1, "mode": config.mode, "status": "failed", "error_type": type(exc).__name__, "error": str(exc)})
        raise
    _write_jsonl(config.run_dir / "raw_pce_outcomes.jsonl", outcomes)
    completed = sum(item.get("status") == "completed" for item in outcomes)
    task_outcomes = Counter()
    reasons = Counter()
    for item in outcomes:
        evaluator = item.get("evaluator_result")
        if isinstance(evaluator, dict):
            task_outcomes[str(evaluator.get("task_outcome", "unknown"))] += 1
            reasons[str(evaluator.get("outcome_reason", "unclassified"))] += 1
        else:
            task_outcomes["incomplete"] += 1
            reasons["attempts_exhausted"] += 1
    summary = {
        "schema_version": 1, "mode": config.mode,
        "status": "completed" if completed == len(outcomes) else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(), "instances": len(outcomes),
        "completed_instances": completed, "incomplete_instances": len(outcomes) - completed,
        "task_outcomes": dict(sorted(task_outcomes.items())),
        "outcome_reasons": dict(sorted(reasons.items())),
        "final_validation_labels_assigned": 0,
    }
    atomic_json(config.run_dir / "summary.json", summary)
    atomic_json(status_path, summary)
    return summary
