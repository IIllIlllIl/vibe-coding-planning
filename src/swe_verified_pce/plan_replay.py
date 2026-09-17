"""Run Code+Evaluate from externally frozen, recovered SWE-Verified Plans."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import atomic_json
from src.swe_verified_pce.config import SWEVerifiedPCEConfig
from src.swe_verified_pce.dataset import file_sha256, load_swe_verified_pce_cases
from src.swe_verified_pce.hpc_executor import (
    SWEVerifiedPCEHPCExecutor,
    execution_fingerprint,
)
from src.swe_verified_pce.runner import checkpoint_identity


def _stable(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class _RecoveredPlanExecutor(SWEVerifiedPCEHPCExecutor):
    def __init__(
        self,
        config: SWEVerifiedPCEConfig,
        *,
        replay_manifest_sha256: str,
        plans: dict[str, str],
    ) -> None:
        super().__init__(config)
        self.replay_manifest_sha256 = replay_manifest_sha256
        self.plans = plans

    def execution_fingerprint(self, cases: Sequence[Any]) -> str:
        return _stable(
            {
                "schema": 1,
                "mode": "swe_verified_recovered_plan_ce_replay",
                "base_pce_fingerprint": execution_fingerprint(self.config, cases),
                "replay_manifest_sha256": self.replay_manifest_sha256,
                "plans": [
                    {
                        "instance_id": case.instance_id,
                        "plan_sha256": text_sha256(self.plans[case.instance_id]),
                    }
                    for case in cases
                ],
            }
        )

    def _prepare(self, batch_dir, fingerprint, cases):  # type: ignore[no-untyped-def]
        tasks = super()._prepare(batch_dir, fingerprint, cases)
        for task, case in zip(tasks, cases, strict=True):
            checkpoint_dir = batch_dir / "checkpoints" / f"task_{task.index:04d}"
            payload = {
                "schema_version": 1,
                "checkpoint_identity": checkpoint_identity(
                    case, execution_fingerprint=fingerprint
                ),
                "phase": "plan",
                "payload": {
                    "plan": self.plans[case.instance_id],
                    "trajectory": [],
                    "source": "frozen_recovered_plan",
                },
            }
            path = checkpoint_dir / "plan.json"
            if path.is_file():
                if json.loads(path.read_text(encoding="utf-8")) != payload:
                    raise ValueError(
                        f"recovered Plan checkpoint differs: {case.instance_id}"
                    )
            else:
                atomic_json(path, payload)
        return tasks


def run_recovered_plan_ce_replay(
    config: SWEVerifiedPCEConfig,
    replay_manifest_path: Path,
) -> dict[str, Any] | None:
    """Advance one resume-safe recovered-Plan Code+Evaluate controller slice."""
    replay_manifest_path = replay_manifest_path.resolve()
    manifest = json.loads(replay_manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("purpose") != "swe_verified_recovered_plan_ce_replay"
    ):
        raise ValueError("invalid recovered-Plan CE replay manifest")
    if manifest.get("source_manifest_sha256") != file_sha256(
        config.dataset_snapshot / "manifest.json"
    ):
        raise ValueError("recovered-Plan replay source snapshot differs")
    if config.selection_manifest is None:
        raise ValueError("recovered-Plan replay requires a frozen selection")
    if manifest.get("selection_manifest_sha256") != file_sha256(
        config.selection_manifest
    ):
        raise ValueError("recovered-Plan replay selection differs")
    if manifest.get("image_manifest_sha256") != file_sha256(config.image_manifest):
        raise ValueError("recovered-Plan replay image manifest differs")
    entries = manifest.get("recovered_plans")
    if not isinstance(entries, list) or not entries:
        raise ValueError("recovered-Plan replay requires recovered_plans")
    plans: dict[str, str] = {}
    for entry in entries:
        instance_id = str(entry.get("instance_id", ""))
        plan = entry.get("plan")
        if (
            not instance_id
            or not isinstance(plan, str)
            or not plan.strip()
            or text_sha256(plan) != entry.get("plan_sha256")
        ):
            raise ValueError(f"invalid frozen recovered Plan: {instance_id}")
        plans[instance_id] = plan
    if len(plans) != len(entries):
        raise ValueError("recovered-Plan replay instance IDs must be unique")

    cases, _, _ = load_swe_verified_pce_cases(
        config.dataset_snapshot, config.image_manifest
    )
    by_id = {case.instance_id: case for case in cases}
    if tuple(plans) != config.instance_ids:
        raise ValueError("recovered Plan order differs from frozen selection")
    missing = [instance_id for instance_id in plans if instance_id not in by_id]
    if missing:
        raise ValueError("recovered Plans lack frozen images: " + ", ".join(missing))
    selected = [by_id[instance_id] for instance_id in plans]
    config.run_dir.mkdir(parents=True, exist_ok=True)
    replay_sha = file_sha256(replay_manifest_path)
    executor = _RecoveredPlanExecutor(
        config, replay_manifest_sha256=replay_sha, plans=plans
    )
    fingerprint = executor.execution_fingerprint(selected)
    run_manifest = {
        "schema_version": 1,
        "mode": "swe_verified_recovered_plan_ce_replay",
        "project_git_head": os.environ.get("VIBE_PROJECT_GIT_HEAD", ""),
        "contains_plan_agent": False,
        "contains_checker": False,
        "contains_code_agent": True,
        "contains_official_evaluator": True,
        "config_sha256": file_sha256(config.config_path),
        "replay_manifest_sha256": replay_sha,
        "image_manifest_sha256": file_sha256(config.image_manifest),
        "execution_fingerprint": fingerprint,
        "instance_ids": list(plans),
        "plan_sha256": {
            instance_id: text_sha256(plan) for instance_id, plan in plans.items()
        },
        "attribution": "independent_code_sampling_from_recovered_plan",
    }
    path = config.run_dir / "run_manifest.json"
    if path.is_file() and json.loads(path.read_text(encoding="utf-8")) != run_manifest:
        raise ValueError("recovered-Plan replay run manifest differs")
    atomic_json(path, run_manifest)
    status = config.run_dir / "controller_status.json"
    atomic_json(
        status,
        {
            "schema_version": 1,
            "mode": "swe_verified_recovered_plan_ce_replay",
            "status": "running",
            "execution_fingerprint": fingerprint,
            "tasks": len(selected),
        },
    )
    try:
        outcomes = executor.evaluate(selected)
    except ControllerYield as exc:
        atomic_json(
            status,
            {
                "schema_version": 1,
                "status": "yielded",
                "worker_job_id": exc.job_id,
                "batch_dir": exc.batch_dir,
            },
        )
        return None

    output = config.run_dir / "ce_outcomes.jsonl"
    temporary = output.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in outcomes),
        encoding="utf-8",
    )
    temporary.replace(output)
    counts: Counter[str] = Counter()
    for row in outcomes:
        evaluator = row.get("evaluator_result")
        if row.get("status") != "completed" or not isinstance(evaluator, dict):
            counts["operationally_incomplete"] += 1
        else:
            counts[str(evaluator.get("task_outcome", "unknown"))] += 1
    result = {
        "schema_version": 1,
        "mode": "swe_verified_recovered_plan_ce_replay",
        "status": (
            "completed_with_incomplete"
            if counts["operationally_incomplete"]
            else "completed"
        ),
        "instances": len(outcomes),
        "outcomes": dict(sorted(counts.items())),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(config.run_dir / "result.json", result)
    atomic_json(status, result)
    return result
