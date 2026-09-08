"""Replay Code and Evaluate from frozen accepted PCCE Plans."""

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
from src.polybench_pcce.hpc_executor import _case_dict, build_array_script
from src.polybench_pce.dataset import file_sha256
from src.polybench_pce.hpc_executor import pce_semantic_sha256
from src.polybench_pce.runner import checkpoint_identity


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def prepare_ce_replay(
    config: PolyBenchPCCEConfig,
    *,
    replay_id: str,
    manifest_path: Path,
) -> tuple[Path, str, list[TaskFiles]]:
    """Freeze accepted Plans into a new Code+Evaluate task identity."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", replay_id):
        raise ValueError("replay_id must match [A-Za-z0-9_.-]+")
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("purpose") != (
        "polybench_pcce_accepted_plan_ce_replay"
    ):
        raise ValueError("invalid accepted-Plan CE replay manifest")
    source_manifest = json.loads(
        (config.run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    if source_manifest.get("pcce_semantic_sha256") != manifest.get(
        "source_pcce_semantic_sha256"
    ):
        raise ValueError("CE replay source PCCE semantic identity mismatch")
    cases, _ = load_pcce_cases(config)
    case_by_id = {case.instance_id: case for case in cases}
    entries = manifest.get("accepted_plans")
    if not isinstance(entries, list) or not entries:
        raise ValueError("CE replay manifest requires accepted_plans")
    if len({str(row.get("instance_id")) for row in entries}) != len(entries):
        raise ValueError("CE replay manifest instance IDs must be unique")

    frozen: list[tuple[Any, Path, str]] = []
    for row in entries:
        instance_id = str(row["instance_id"])
        if instance_id not in case_by_id:
            raise ValueError(f"CE replay case is outside the source universe: {instance_id}")
        review_path = config.run_dir / str(row["accepted_review_relpath"])
        if file_sha256(review_path) != row.get("accepted_review_sha256"):
            raise ValueError(f"accepted review hash mismatch: {instance_id}")
        review = json.loads(review_path.read_text(encoding="utf-8"))
        checker = review.get("checker_output")
        plan = review.get("plan")
        if (
            review.get("status") != "completed"
            or review.get("instance_id") != instance_id
            or not isinstance(checker, dict)
            or checker.get("should_proceed") is not True
            or not isinstance(plan, str)
            or not plan.strip()
            or text_sha256(plan) != row.get("accepted_plan_sha256")
        ):
            raise ValueError(f"invalid accepted review evidence: {instance_id}")
        frozen.append((case_by_id[instance_id], review_path, plan))

    fingerprint = _hash(
        {
            "schema": 1,
            "mode": "polybench_pcce_accepted_plan_ce_replay",
            "replay_id": replay_id,
            "manifest_sha256": file_sha256(manifest_path),
            "source_pcce_semantic_sha256": source_manifest["pcce_semantic_sha256"],
            "runtime_pce_semantic_sha256": pce_semantic_sha256(config.pce),
            "plans": [
                {"instance_id": case.instance_id, "plan_sha256": text_sha256(plan)}
                for case, _, plan in frozen
            ],
        }
    )
    replay_root = config.run_dir / "ce_replays" / replay_id
    batch_dir = replay_root / "hpc_tasks" / "ce" / fingerprint
    tasks: list[TaskFiles] = []
    for index, (case, review_path, plan) in enumerate(frozen):
        checkpoint_dir = batch_dir / "checkpoints" / f"task_{index:04d}"
        identity = checkpoint_identity(case.source, execution_fingerprint=fingerprint)
        atomic_json(
            checkpoint_dir / "plan.json",
            {
                "schema_version": 1,
                "checkpoint_identity": identity,
                "phase": "plan",
                "payload": {"plan": plan, "trajectory": []},
            },
        )
        task_manifest = batch_dir / "tasks" / f"task_{index:04d}.json"
        payload = {
            "schema_version": 1,
            "mode": "polybench_pcce",
            "phase": "ce",
            "fingerprint": fingerprint,
            "task_index": index,
            "instance_id": case.instance_id,
            "case": _case_dict(case),
            "accepted_review_relpath": str(review_path.relative_to(config.run_dir)),
            "accepted_plan": plan,
            "accepted_plan_sha256": text_sha256(plan),
        }
        if task_manifest.is_file():
            if json.loads(task_manifest.read_text(encoding="utf-8")) != payload:
                raise ValueError(f"CE replay task differs: {case.instance_id}")
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
    frozen_manifest = {
        "schema_version": 1,
        "mode": "polybench_pcce_accepted_plan_ce_replay",
        "replay_id": replay_id,
        "fingerprint": fingerprint,
        "source_manifest_sha256": file_sha256(config.run_dir / "run_manifest.json"),
        "input_manifest_sha256": file_sha256(manifest_path),
        "runtime_pce_semantic_sha256": pce_semantic_sha256(config.pce),
        "instance_ids": [task.instance_id for task in tasks],
    }
    path = replay_root / "run_manifest.json"
    if path.is_file() and json.loads(path.read_text(encoding="utf-8")) != frozen_manifest:
        raise ValueError("CE replay run manifest differs")
    atomic_json(path, frozen_manifest)
    atomic_json(batch_dir / "manifest.json", {**frozen_manifest, "task_count": len(tasks)})
    return batch_dir, fingerprint, tasks


def run_ce_replay(
    config: PolyBenchPCCEConfig, *, replay_id: str, manifest_path: Path
) -> dict[str, Any] | None:
    batch_dir, fingerprint, tasks = prepare_ce_replay(
        config, replay_id=replay_id, manifest_path=manifest_path
    )
    root = config.run_dir / "ce_replays" / replay_id
    status = root / "controller_status.json"

    def write_script(indices: Sequence[int], attempt: int) -> Path:
        path = batch_dir / f"ce_array_attempt_{attempt:02d}.sbatch"
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
        if value.get("fingerprint") != fingerprint or value.get("instance_id") != task.instance_id:
            raise ValueError("CE replay output identity mismatch")
        if value.get("pcce_status") != "completed" or not isinstance(
            value.get("evaluator_result"), dict
        ):
            raise ValueError("CE replay output lacks evaluator result")

    try:
        outputs = SlurmTaskBatch(config.hpc).run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: f"{config.hpc.job_name_prefix}-replay-a{attempt}",
            write_script=write_script,
            validate_output=validate,
        )
    except ControllerYield as exc:
        atomic_json(status, {"schema_version": 1, "status": "yielded", "worker_job_id": exc.job_id, "batch_dir": exc.batch_dir})
        return None

    _write_jsonl(root / "ce_outcomes.jsonl", outputs)
    counts: Counter[str] = Counter()
    for row in outputs:
        evaluator = row.get("evaluator_result")
        if row.get("status") != "completed" or not isinstance(evaluator, dict):
            counts["operationally_incomplete"] += 1
            continue
        value = evaluator.get("evaluator_resolved")
        counts[
            "resolved" if value is True else "unresolved" if value is False else "unknown"
        ] += 1
    result = {
        "schema_version": 1,
        "mode": "polybench_pcce_accepted_plan_ce_replay",
        "status": (
            "completed_with_incomplete"
            if counts["operationally_incomplete"]
            else "completed"
        ),
        "instances": len(tasks),
        **dict(counts),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(root / "result.json", result)
    atomic_json(status, result)
    return result
