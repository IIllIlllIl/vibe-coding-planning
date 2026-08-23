"""Re-evaluate preserved PCCE Code checkpoints without rerunning PC or Code."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskFiles, atomic_json
from src.polybench_pcce.config import PolyBenchPCCEConfig
from src.polybench_pcce.dataset import load_pcce_cases
from src.polybench_pcce.hpc_executor import build_array_script
from src.polybench_pcce.models import PCCECase
from src.polybench_pce.evaluator_resume import _copy_checkpoint
from src.polybench_pce.hpc_executor import pce_semantic_sha256
from src.polybench_pce.runner import checkpoint_identity


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
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


def _case_dict(case: PCCECase) -> dict[str, Any]:
    return {
        "source": case.source.to_dict(),
        "baseline_plan": case.baseline_plan,
        "baseline_resolved": case.baseline_resolved,
        "baseline_outcome_sha256": case.baseline_outcome_sha256,
    }


def _prepare(
    config: PolyBenchPCCEConfig,
    cases: Sequence[PCCECase],
    *,
    repair_id: str,
    instance_ids: Sequence[str] | None = None,
) -> tuple[Path, str, list[TaskFiles], list[dict[str, Any]]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repair_id):
        raise ValueError("repair_id must match [A-Za-z0-9_.-]+")
    source_manifest_path = config.run_dir / "run_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("mode") != "polybench_pcce":
        raise ValueError("source run is not PolyBench PCCE")
    source_ce_path = config.run_dir / "ce_outcomes.jsonl"
    source_ce = _read_jsonl(source_ce_path)
    fingerprints = {str(row.get("fingerprint", "")) for row in source_ce}
    if len(fingerprints) != 1 or not next(iter(fingerprints)):
        raise ValueError("source PCCE CE outcomes lack one stable fingerprint")
    source_fingerprint = next(iter(fingerprints))
    source_batch = config.run_dir / "hpc_tasks" / "ce" / source_fingerprint
    source_batch_manifest = json.loads(
        (source_batch / "manifest.json").read_text(encoding="utf-8")
    )
    source_ids = [str(value) for value in source_batch_manifest["instance_ids"]]
    if source_ids != [str(row.get("instance_id")) for row in source_ce]:
        raise ValueError("source PCCE CE batch and outcomes differ")
    case_by_id = {case.instance_id: case for case in cases}
    if not set(source_ids).issubset(case_by_id):
        raise ValueError("source PCCE CE outcomes are outside the frozen case set")
    requested = list(instance_ids or [])
    if len(requested) != len(set(requested)):
        raise ValueError("PCCE evaluator repair instance_ids must be unique")
    requested_set = set(requested)
    unknown = sorted(requested_set - set(case_by_id))
    if unknown:
        raise ValueError(
            "PCCE evaluator repair requested unknown instance_ids: "
            + ", ".join(unknown)
        )
    missing_ce = sorted(requested_set - set(source_ids))
    if missing_ce:
        raise ValueError(
            "PCCE evaluator repair requested instances without CE evidence: "
            + ", ".join(missing_ce)
        )
    selected = [
        (index, instance_id, source_output)
        for index, (instance_id, source_output) in enumerate(
            zip(source_ids, source_ce, strict=True)
        )
        if not requested or instance_id in requested_set
    ]
    selected_ids = [instance_id for _, instance_id, _ in selected]

    repair_fingerprint = _hash(
        {
            "schema": 1,
            "mode": "polybench_pcce_evaluator_resume",
            "repair_id": repair_id,
            "source_pcce_semantic_sha256": source_manifest["pcce_semantic_sha256"],
            "source_ce_fingerprint": source_fingerprint,
            "evaluator_semantic_sha256": pce_semantic_sha256(config.pce),
            "instances": [
                {
                    "instance_id": row["instance_id"],
                    "accepted_plan_sha256": text_sha256(str(row["plan"])),
                }
                for _, _, row in selected
            ],
        }
    )
    repair_root = config.run_dir / "evaluator_repairs" / repair_id
    batch_dir = repair_root / "hpc_tasks" / "evaluate" / repair_fingerprint
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pcce_evaluator_resume",
        "repair_id": repair_id,
        "repair_fingerprint": repair_fingerprint,
        "source_ce_fingerprint": source_fingerprint,
        "source_pcce_semantic_sha256": source_manifest["pcce_semantic_sha256"],
        "evaluator_semantic_sha256": pce_semantic_sha256(config.pce),
        "selected_instance_ids": selected_ids,
        "excluded_without_ce": [
            case.instance_id
            for case in cases
            if case.instance_id not in set(source_ids)
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = repair_root / "run_manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        comparable = {
            key: value for key, value in manifest.items() if key != "created_at"
        }
        existing_comparable = {
            key: value for key, value in existing.items() if key != "created_at"
        }
        if existing_comparable != comparable:
            raise ValueError("PCCE evaluator repair manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)

    tasks: list[TaskFiles] = []
    for index, instance_id, source_output in selected:
        case = case_by_id[instance_id]
        source_task_path = source_batch / "tasks" / f"task_{index:04d}.json"
        source_task = json.loads(source_task_path.read_text(encoding="utf-8"))
        if (
            source_task.get("fingerprint") != source_fingerprint
            or source_task.get("instance_id") != instance_id
            or source_output.get("status") != "completed"
            or source_output.get("pcce_status") != "completed"
        ):
            raise ValueError(f"invalid source PCCE CE evidence: {instance_id}")
        accepted_plan = source_task.get("accepted_plan")
        accepted_review_relpath = source_task.get("accepted_review_relpath")
        if (
            not isinstance(accepted_plan, str)
            or not accepted_plan.strip()
            or source_output.get("plan") != accepted_plan
            or not isinstance(accepted_review_relpath, str)
            or not (config.run_dir / accepted_review_relpath).is_file()
        ):
            raise ValueError(f"invalid accepted-plan source evidence: {instance_id}")
        accepted_review = json.loads(
            (config.run_dir / accepted_review_relpath).read_text(encoding="utf-8")
        )
        checker_output = accepted_review.get("checker_output")
        if (
            accepted_review.get("status") != "completed"
            or accepted_review.get("instance_id") != instance_id
            or accepted_review.get("plan") != accepted_plan
            or not isinstance(checker_output, dict)
            or checker_output.get("should_proceed") is not True
        ):
            raise ValueError(f"invalid accepted-review source evidence: {instance_id}")
        source_checkpoints = source_batch / "checkpoints" / f"task_{index:04d}"
        if not all(
            (source_checkpoints / f"{phase}.json").is_file()
            for phase in ("plan", "code")
        ):
            raise ValueError(f"source PCCE CE checkpoint is incomplete: {instance_id}")
        source_identity = checkpoint_identity(
            case.source, execution_fingerprint=source_fingerprint
        )
        target_identity = checkpoint_identity(
            case.source, execution_fingerprint=repair_fingerprint
        )
        checkpoint_dir = batch_dir / "checkpoints" / f"task_{index:04d}"
        for phase in ("plan", "code"):
            _copy_checkpoint(
                source_checkpoints / f"{phase}.json",
                checkpoint_dir / f"{phase}.json",
                phase=phase,
                source_identity=source_identity,
                target_identity=target_identity,
            )
        task_manifest = batch_dir / "tasks" / f"task_{index:04d}.json"
        payload = {
            "schema_version": 1,
            "mode": "polybench_pcce",
            "phase": "ce",
            "fingerprint": repair_fingerprint,
            "task_index": index,
            "instance_id": instance_id,
            "case": _case_dict(case),
            "accepted_review_relpath": accepted_review_relpath,
            "accepted_plan": accepted_plan,
            "accepted_plan_sha256": text_sha256(accepted_plan),
        }
        if task_manifest.is_file():
            if json.loads(task_manifest.read_text(encoding="utf-8")) != payload:
                raise ValueError(f"PCCE evaluator repair task differs: {instance_id}")
        else:
            atomic_json(task_manifest, payload)
        tasks.append(
            TaskFiles(
                index=index,
                instance_id=instance_id,
                manifest_path=task_manifest,
                output_path=batch_dir / "outputs" / f"task_{index:04d}.json",
                attempts_dir=batch_dir / "attempts" / f"task_{index:04d}",
            )
        )
    atomic_json(batch_dir / "manifest.json", {**manifest, "task_count": len(tasks)})
    return batch_dir, repair_fingerprint, tasks, [row for _, _, row in selected]


def resume_polybench_pcce_evaluator(
    config: PolyBenchPCCEConfig,
    *,
    repair_id: str,
    instance_ids: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """Submit or collect Evaluate-only tasks from fixed PCCE Code checkpoints."""
    cases, _ = load_pcce_cases(config)
    batch_dir, fingerprint, tasks, _ = _prepare(
        config, cases, repair_id=repair_id, instance_ids=instance_ids
    )
    repair_root = config.run_dir / "evaluator_repairs" / repair_id
    status_path = repair_root / "controller_status.json"
    atomic_json(
        status_path,
        {
            "schema_version": 1,
            "mode": "polybench_pcce_evaluator_resume",
            "status": "running",
        },
    )

    def write_script(indices: Sequence[int], attempt: int) -> Path:
        path = batch_dir / f"evaluate_array_attempt_{attempt:02d}.sbatch"
        path.write_text(
            build_array_script(
                config=config,
                batch_dir=batch_dir,
                indices=indices,
                attempt=attempt,
                phase="ce",
            ),
            encoding="utf-8",
        )
        return path

    def validate(task: TaskFiles, value: dict[str, Any]) -> None:
        if value.get("fingerprint") != fingerprint:
            raise ValueError("PCCE evaluator repair output fingerprint mismatch")
        if value.get("instance_id") != task.instance_id:
            raise ValueError("PCCE evaluator repair output instance mismatch")
        evaluator = value.get("evaluator_result")
        if value.get("pcce_status") != "completed" or not isinstance(evaluator, dict):
            raise ValueError("PCCE evaluator repair lacks completed evaluator evidence")
        if evaluator.get("test_returncode") in {126, 127}:
            raise ValueError("command-not-executed result cannot complete repair")

    try:
        outputs = SlurmTaskBatch(config.hpc).run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: (
                f"{config.hpc.job_name_prefix}-eval-{batch_dir.name[:10]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "polybench_pcce_evaluator_resume",
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
                "mode": "polybench_pcce_evaluator_resume",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    _write_jsonl(repair_root / "raw_evaluator_outcomes.jsonl", outputs)
    outcome_counts: Counter[str] = Counter()
    for row in outputs:
        resolved = row["evaluator_result"].get("evaluator_resolved")
        outcome_counts[
            "resolved"
            if resolved is True
            else "unresolved"
            if resolved is False
            else "unknown"
        ] += 1
    source_method_rows = _read_jsonl(config.run_dir / "pcce_outcomes.jsonl")
    source_rejected = sum(
        row.get("method_status") == "checker_rejected_after_3_reviews"
        for row in source_method_rows
    )
    selected_ids = {task.instance_id for task in tasks}
    selected_rejected = sum(
        row.get("method_status") == "checker_rejected_after_3_reviews"
        and row.get("instance_id") in selected_ids
        for row in source_method_rows
    )
    summary = {
        "schema_version": 1,
        "mode": "polybench_pcce_evaluator_resume",
        "status": "completed",
        "repair_id": repair_id,
        "repair_fingerprint": fingerprint,
        "instances": len(cases),
        "selected_instances": len(tasks),
        "evaluated_instances": len(outputs),
        "checker_rejected_without_code": selected_rejected,
        "source_checker_rejected_without_code": source_rejected,
        "resolved": outcome_counts["resolved"],
        "unresolved": outcome_counts["unresolved"],
        "unknown": outcome_counts["unknown"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(repair_root / "result.json", summary)
    atomic_json(status_path, summary)
    return summary
