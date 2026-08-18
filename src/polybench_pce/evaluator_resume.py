"""Re-evaluate preserved PolyBench PCE Plan/Code checkpoints without LLM calls."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

from src.exceptions import ControllerYield
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskFiles, atomic_json
from src.polybench_pce.config import PolyBenchPCEConfig
from src.polybench_pce.dataset import load_polybench_pce_cases
from src.polybench_pce.hpc_executor import build_array_script, pce_semantic_sha256
from src.polybench_pce.models import PolyBenchPCECase
from src.polybench_pce.runner import checkpoint_identity


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _copy_checkpoint(
    source: Path,
    target: Path,
    *,
    phase: str,
    source_identity: str,
    target_identity: str,
) -> None:
    value = json.loads(source.read_text(encoding="utf-8"))
    if value.get("phase") != phase or value.get("checkpoint_identity") != source_identity:
        raise ValueError(f"invalid source {phase} checkpoint: {source}")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"source {phase} checkpoint has no payload: {source}")
    atomic_json(
        target,
        {
            "schema_version": 1,
            "checkpoint_identity": target_identity,
            "phase": phase,
            "payload": payload,
        },
    )


def _prepare(
    config: PolyBenchPCEConfig,
    cases: Sequence[PolyBenchPCECase],
    *,
    repair_id: str,
) -> tuple[Path, str, list[TaskFiles], list[str]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repair_id):
        raise ValueError("repair_id must match [A-Za-z0-9_.-]+")
    source_manifest = json.loads(
        (config.run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    source_fingerprint = str(source_manifest["execution_fingerprint"])
    source_batch = config.run_dir / "hpc_tasks" / "pce" / source_fingerprint
    if not source_batch.is_dir():
        raise ValueError(f"source PCE batch is missing: {source_batch}")
    repair_fingerprint = _hash(
        {
            "schema": 1,
            "mode": "polybench_pce_evaluator_resume",
            "repair_id": repair_id,
            "source_execution_fingerprint": source_fingerprint,
            "evaluator_semantic_sha256": pce_semantic_sha256(config),
            "instances": [
                {"instance_id": case.instance_id, "row_sha256": case.row_sha256}
                for case in cases
            ],
        }
    )
    repair_root = config.run_dir / "evaluator_repairs" / repair_id
    batch_dir = repair_root / "hpc_tasks" / "evaluate" / repair_fingerprint
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pce_evaluator_resume",
        "repair_id": repair_id,
        "source_execution_fingerprint": source_fingerprint,
        "repair_fingerprint": repair_fingerprint,
        "evaluator_semantic_sha256": pce_semantic_sha256(config),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = repair_root / "run_manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        comparable = {k: v for k, v in manifest.items() if k != "created_at"}
        existing_comparable = {k: v for k, v in existing.items() if k != "created_at"}
        if existing_comparable != comparable:
            raise ValueError("evaluator repair manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)

    tasks: list[TaskFiles] = []
    skipped: list[str] = []
    for index, case in enumerate(cases):
        source_checkpoints = source_batch / "checkpoints" / f"task_{index:04d}"
        if not (source_checkpoints / "code.json").is_file():
            skipped.append(case.instance_id)
            continue
        source_identity = checkpoint_identity(
            case, execution_fingerprint=source_fingerprint
        )
        target_identity = checkpoint_identity(
            case, execution_fingerprint=repair_fingerprint
        )
        checkpoint_dir = batch_dir / "checkpoints" / f"task_{index:04d}"
        _copy_checkpoint(
            source_checkpoints / "plan.json",
            checkpoint_dir / "plan.json",
            phase="plan",
            source_identity=source_identity,
            target_identity=target_identity,
        )
        _copy_checkpoint(
            source_checkpoints / "code.json",
            checkpoint_dir / "code.json",
            phase="code",
            source_identity=source_identity,
            target_identity=target_identity,
        )
        task_manifest = batch_dir / "tasks" / f"task_{index:04d}.json"
        payload = {
            "schema_version": 1,
            "mode": "polybench_pce",
            "fingerprint": repair_fingerprint,
            "task_index": index,
            "instance_id": case.instance_id,
            "case": case.to_dict(),
        }
        if task_manifest.is_file():
            if json.loads(task_manifest.read_text(encoding="utf-8")) != payload:
                raise ValueError(f"evaluator repair task differs: {task_manifest}")
        else:
            atomic_json(task_manifest, payload)
        tasks.append(
            TaskFiles(
                index=index,
                instance_id=case.instance_id,
                manifest_path=task_manifest,
                output_path=batch_dir / "outputs" / f"task_{index:04d}.json",
                attempts_dir=batch_dir / "attempts" / f"task_{index:04d}",
            )
        )
    atomic_json(
        batch_dir / "manifest.json",
        {
            **manifest,
            "task_count": len(tasks),
            "instance_ids": [task.instance_id for task in tasks],
            "skipped_without_code_checkpoint": skipped,
        },
    )
    return batch_dir, repair_fingerprint, tasks, skipped


def resume_polybench_pce_evaluator(
    config: PolyBenchPCEConfig, *, repair_id: str
) -> dict[str, Any] | None:
    """Submit/collect a new evaluator batch from validated old Plan/Code state."""
    cases, _, _ = load_polybench_pce_cases(
        config.dataset_snapshot, config.image_manifest
    )
    batch_dir, fingerprint, tasks, skipped = _prepare(
        config, cases, repair_id=repair_id
    )

    def write_script(indices: Sequence[int], attempt: int) -> Path:
        path = batch_dir / f"evaluate_array_attempt_{attempt:02d}.sbatch"
        path.write_text(
            build_array_script(
                config=config,
                batch_dir=batch_dir,
                indices=indices,
                attempt=attempt,
            ),
            encoding="utf-8",
        )
        return path

    def validate(task: TaskFiles, value: dict[str, Any]) -> None:
        if value.get("fingerprint") != fingerprint:
            raise ValueError("evaluator repair output fingerprint mismatch")
        if value.get("instance_id") != task.instance_id:
            raise ValueError("evaluator repair output instance mismatch")
        evaluator = value.get("evaluator_result")
        if not isinstance(evaluator, dict):
            raise ValueError("evaluator repair output lacks evaluator evidence")
        if evaluator.get("test_returncode") in {126, 127}:
            raise ValueError("command-not-executed result cannot complete repair")

    try:
        outputs = SlurmTaskBatch(config.hpc).run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: (
                f"{config.hpc.job_name_prefix}-{batch_dir.name[:12]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
    except ControllerYield:
        return None

    repair_root = config.run_dir / "evaluator_repairs" / repair_id
    outcomes_path = repair_root / "raw_evaluator_outcomes.jsonl"
    temporary = outcomes_path.with_suffix(".jsonl.tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in outputs
        ),
        encoding="utf-8",
    )
    temporary.replace(outcomes_path)
    summary = {
        "schema_version": 1,
        "mode": "polybench_pce_evaluator_resume",
        "status": "completed",
        "repair_id": repair_id,
        "repair_fingerprint": fingerprint,
        "evaluated_instances": len(outputs),
        "skipped_without_code_checkpoint": skipped,
        "resolved": sum(
            bool(row["evaluator_result"].get("evaluator_resolved")) for row in outputs
        ),
        "unresolved": sum(
            row["evaluator_result"].get("evaluator_resolved") is False
            for row in outputs
        ),
        "unknown": sum(
            row["evaluator_result"].get("evaluator_resolved") is None
            for row in outputs
        ),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(repair_root / "result.json", summary)
    return summary
