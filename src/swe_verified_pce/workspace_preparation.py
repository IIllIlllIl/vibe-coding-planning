"""No-model workspace handoff over the existing PCE Worker and SlurmTaskBatch.

Prepared trees are atomically moved, never copied in an Agent allocation.
Only complete handoffs live outside disposable ``workspaces`` directories.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from src.exceptions import ControllerYield, FatalError
from src.optimization.hpc.phase_wave import prepare_phase_wave
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskAttemptsExhausted, atomic_json


PHASES = ("plan", "code", "evaluate")


def prepared_repository_path(run_dir: Path, relative: str) -> Path:
    path = Path(relative)
    base = (run_dir / "hpc_tasks" / "pce_prepare").resolve()
    candidate = run_dir / path
    if path.is_absolute() or ".." in path.parts or candidate.is_symlink():
        raise FatalError("invalid prepared workspace path")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        raise FatalError("prepared workspace is outside this run's preparation authority")
    parts = resolved.relative_to(base).parts
    if (len(parts) != 5 or parts[1] != "attempts" or not parts[2].startswith("task_")
            or not parts[3].startswith("attempt_") or parts[4] != "prepared_repository"):
        raise FatalError("unexpected prepared workspace depth")
    return candidate


def claim_workspace(runner, case, phase: str, target: Path) -> None:
    """Validate an exact handoff and atomically move its tree into this attempt."""
    manifest = json.loads((runner.attempt_dir / "workspace_preparation.json").read_text())
    if (manifest.get("schema_version") != 1
            or manifest.get("checkpoint_identity") != runner.checkpoint_identity
            or manifest.get("instance_id") != case.instance_id
            or manifest.get("base_commit") != case.base_commit
            or manifest.get("sif_sha256") != case.image.sif_sha256
            or manifest.get("target_attempt_relative_path") != runner.attempt_dir.relative_to(runner.config.run_dir).as_posix()):
        raise FatalError("workspace preparation handoff identity mismatch")
    record = manifest["phases"][phase]
    if record.get("workspace_status") != "ready":
        raise FatalError("required PCE workspace was not prepared; inline fallback is disabled")
    source = prepared_repository_path(runner.config.run_dir, record["prepared_repository_relative_path"])
    if not source.is_dir() or target.exists() or target.is_symlink():
        raise FatalError("prepared workspace is missing, already consumed, or target is occupied")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)  # Same scratch filesystem; never fall back to a bulk copy.
    baseline = source.parent / "repository_baselines" / phase
    if baseline.is_dir():
        shutil.copytree(baseline, runner.attempt_dir / "repository_baselines" / phase)
    atomic_json(runner.attempt_dir / "workspace_claims" / f"{phase}.json", {
        "schema_version": 1, "phase": phase,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "source_relative_path": record["prepared_repository_relative_path"],
        "target_relative_path": target.relative_to(runner.config.run_dir).as_posix(),
    })


def finalize_preparation_failure(task, value):
    """A recorded, exhausted setup failure is NoScore, never an unresolved Plan."""
    if value.get("outcome_reason") != "workspace_preparation_exhausted":
        return SlurmTaskBatch._finalize_known_case_failure(
            task, value, fingerprint=json.loads(task.manifest_path.read_text())["fingerprint"])
    manifest = json.loads(task.manifest_path.read_text())
    if (value.get("status") != "blocking_failed"
            or value.get("fingerprint") != manifest["fingerprint"]
            or value.get("mode") != manifest["mode"]
            or value.get("task_index") != task.index
            or value.get("instance_id") != task.instance_id
            or value.get("task_outcome") != "unknown"
            or value.get("final_validation_label") is not None
            or not value.get("preparation_evidence")):
        return None
    return {**value, "status": "incomplete", "pce_status": "incomplete", "retry_disposition": "no_retry"}


def prepare_before_pce(executor, pce_batch, pce_fingerprint, pce_tasks, indices, pce_attempt):
    """Called from the existing write-script boundary, before PCE submission.

    A preparation yield does not submit a PCE job or consume a PCE attempt.
    One immutable preparation wave covers the originally requested indices;
    a later call may request its surviving subset after case-local failures.
    """
    from src.swe_verified_pce.hpc_executor import build_array_script
    from src.swe_verified_pce.runner import checkpoint_identity

    config = executor.config
    fingerprint = hashlib.sha256(json.dumps(
        {"pce_fingerprint": pce_fingerprint, "pce_attempt": pce_attempt,
         "operation": "prepare_workspace_v1"}, sort_keys=True).encode()).hexdigest()
    batch = config.run_dir / "hpc_tasks" / "pce_prepare" / fingerprint
    binding_path = batch / "manifest.json"
    original_indices = list(indices)
    if binding_path.exists():
        binding = json.loads(binding_path.read_text())
        original_indices = binding["pce_task_indices"]
        if (binding["fingerprint"] != fingerprint
                or not set(indices).issubset(original_indices)):
            raise ValueError("workspace preparation wave identity mismatch")
    by_index = {task.index: task for task in pce_tasks}
    manifests = []
    for index in original_indices:
        parent = json.loads(by_index[index].manifest_path.read_text())
        for phase in PHASES:
            manifests.append({
                "schema_version": 1, "operation": "prepare_workspace", "mode": executor.mode,
                "fingerprint": fingerprint, "task_index": len(manifests),
                "instance_id": parent["instance_id"], "case": parent["case"],
                "pce_fingerprint": pce_fingerprint, "pce_task_index": index,
                "pce_attempt": pce_attempt, "prepared_phase": phase,
            })
    tasks = prepare_phase_wave(batch_dir=batch, role="pce_prepare", fingerprint=fingerprint,
        manifests=manifests, instance_ids=[row["instance_id"] for row in manifests],
        batch_manifest={"schema_version": 1, "role": "pce_prepare", "fingerprint": fingerprint,
                        "task_count": len(manifests), "pce_task_indices": original_indices})
    prep_config = replace(config, hpc=replace(config.hpc, job_name_prefix=config.hpc.job_name_prefix + "-prep"))

    def write_script(pending, attempt):
        path = batch / f"prepare_array_attempt_{attempt:02d}.sbatch"
        path.write_text(build_array_script(config=prep_config, batch_dir=batch, indices=pending,
            attempt=attempt, worker_module=executor.worker_module,
            label="PCE workspace preparation (no model)", preparation_only=True))
        return path

    def validate(task, value):
        row = manifests[task.index]
        for name in ("fingerprint", "task_index", "instance_id", "pce_fingerprint",
                     "pce_task_index", "pce_attempt", "prepared_phase"):
            if value.get(name) != row[name]:
                raise ValueError(f"workspace preparation output {name} mismatch")
        if value.get("workspace_status") not in {"ready", "checkpoint_reused"}:
            raise ValueError("completed preparation has no valid workspace status")
        if value["workspace_status"] == "ready":
            path = prepared_repository_path(config.run_dir, value["prepared_repository_relative_path"])
            expected = task.attempts_dir / f"attempt_{value['attempt']:02d}" / "prepared_repository"
            if path != expected:
                raise ValueError("prepared workspace does not belong to its preparation attempt")

    results = []
    try:
        results = SlurmTaskBatch(config.hpc).run(batch_dir=batch, fingerprint=fingerprint,
            tasks=tasks, write_script=write_script, validate_output=validate,
            job_name=lambda attempt: f"{prep_config.hpc.job_name_prefix}-{fingerprint[:12]}-a{attempt}")
    except TaskAttemptsExhausted:
        for task in tasks:
            if task.output_path.exists():
                value = json.loads(task.output_path.read_text())
                if value.get("status") == "completed":
                    validate(task, value)
                    results.append(value)
    grouped = {index: {} for index in original_indices}
    for value in results:
        grouped[value["pce_task_index"]][value["prepared_phase"]] = value
    failed = [index for index in original_indices if set(grouped[index]) != set(PHASES)]
    newly_failed = False
    for index in failed:
        task = by_index[index]
        if task.output_path.exists():
            continue
        atomic_json(task.output_path, {
            "schema_version": 1, "status": "blocking_failed", "mode": executor.mode,
            "fingerprint": pce_fingerprint, "task_index": index, "instance_id": task.instance_id,
            "pce_run_index": json.loads(task.manifest_path.read_text()).get("pce_run_index", 1),
            "task_outcome": "unknown", "final_validation_label": None,
            "outcome_reason": "workspace_preparation_exhausted", "operational_failure_scope": "case",
            "preparation_evidence": {"batch_relative_path": batch.relative_to(config.run_dir).as_posix(),
                                     "phases_completed": list(grouped[index])},
        })
        newly_failed = True
        # No PCE task can have consumed these trees: its job was not submitted.
        for value in grouped[index].values():
            if value["workspace_status"] == "ready":
                path = prepared_repository_path(config.run_dir, value["prepared_repository_relative_path"])
                if path.is_dir():
                    shutil.rmtree(path)
    if newly_failed:
        raise ControllerYield(batch_dir=str(pce_batch), job_id=None,
                              reason="workspace_preparation_case_failures_recorded")
    for index in indices:
        if index in failed:
            raise ValueError("failed preparation was not finalized before PCE submission")
        task = by_index[index]
        case = executor.case_from_dict(json.loads(task.manifest_path.read_text())["case"])
        target = task.attempts_dir / f"attempt_{pce_attempt:02d}"
        receipt = {"schema_version": 1, "instance_id": case.instance_id,
            "checkpoint_identity": checkpoint_identity(case, execution_fingerprint=pce_fingerprint),
            "base_commit": case.base_commit, "sif_sha256": case.image.sif_sha256,
            "target_attempt_relative_path": target.relative_to(config.run_dir).as_posix(),
            "phases": grouped[index]}
        path = target / "workspace_preparation.json"
        if path.exists() and json.loads(path.read_text()) != receipt:
            raise ValueError("PCE preparation handoff receipt changed")
        if not path.exists():
            atomic_json(path, receipt)
