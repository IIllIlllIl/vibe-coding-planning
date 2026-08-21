"""Slurm transport for independent PCCE PC waves and CE execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Sequence

from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskAttemptsExhausted,
    TaskFiles,
    atomic_json,
)
from src.optimization.offline_hpc_executor import offline_checker_semantic_sha256
from src.polybench_pcce.config import PolyBenchPCCEConfig
from src.polybench_pcce.models import CEAssignment, PCCECase, PCReviewAssignment
from src.polybench_pce.dataset import file_sha256
from src.polybench_pce.hpc_executor import pce_semantic_sha256


def _stable(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def pcce_semantic_sha256(config: PolyBenchPCCEConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    sources = sorted((root / "src" / "polybench_pcce").glob("*.py"))
    return _stable(
        {
            "schema": 1,
            "sources": {
                str(path.relative_to(root)): file_sha256(path) for path in sources
            },
            "pce_semantic_sha256": pce_semantic_sha256(config.pce),
            "checker_semantic_sha256": offline_checker_semantic_sha256(config.checker),
            "prompts": {
                "checker_system": config.checker_prompt,
                "checker_instance": config.checker_instance_template,
                "plan_revision_system": config.plan_revision_prompt,
                "plan_revision_instance": config.plan_revision_instance_template,
            },
            "max_review_rejections": config.max_review_rejections,
            "guideline_sha256": file_sha256(config.guideline_path),
            "task_attempts": config.hpc.max_task_attempts,
        }
    )


def _case_dict(case: PCCECase) -> dict[str, Any]:
    return {
        "source": case.source.to_dict(),
        "baseline_plan": case.baseline_plan,
        "baseline_resolved": case.baseline_resolved,
        "baseline_outcome_sha256": case.baseline_outcome_sha256,
    }


def build_array_script(
    *,
    config: PolyBenchPCCEConfig,
    batch_dir: Path,
    indices: Sequence[int],
    attempt: int,
    phase: str,
) -> str:
    if not indices:
        raise ValueError("PCCE array requires tasks")
    hpc = config.hpc
    logs = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    logs.mkdir(parents=True, exist_ok=True)
    index_spec = ",".join(str(index) for index in indices)
    job_name = f"{hpc.job_name_prefix}-{phase}-{batch_dir.name[:10]}-a{attempt}"
    return (
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f"#SBATCH --job-name={job_name}",
                f"#SBATCH --partition={hpc.partition}",
                f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
                f"#SBATCH --mem={hpc.mem}",
                f"#SBATCH --time={hpc.time}",
                f"#SBATCH --array={index_spec}",
                f"#SBATCH --output={logs}/%x-%A_%a.out",
                f"#SBATCH --error={logs}/%x-%A_%a.err",
                "set -euo pipefail",
                "set +x",
                f"module load {shlex.quote(hpc.python_module)}",
                f"module load {shlex.quote(hpc.container_module)}",
                f"ENV_FILE={shlex.quote(hpc.remote_env_file)}",
                'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
                'source "${ENV_FILE}"',
                'test -n "${DEEPSEEK_API_KEY:-}" || exit 2',
                f"BATCH_DIR={shlex.quote(str(batch_dir))}",
                'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"',
                f"ATTEMPT={attempt}",
                'ATTEMPT_ID="$(printf "%02d" "${ATTEMPT}")"',
                'TASK_MANIFEST="${BATCH_DIR}/tasks/task_${TASK_ID}.json"',
                'OUTPUT_JSON="${BATCH_DIR}/outputs/task_${TASK_ID}.json"',
                'ATTEMPT_DIR="${BATCH_DIR}/attempts/task_${TASK_ID}/attempt_${ATTEMPT_ID}"',
                'CHECKPOINT_DIR="${BATCH_DIR}/checkpoints/task_${TASK_ID}"',
                'mkdir -p "${ATTEMPT_DIR}" "${CHECKPOINT_DIR}"',
                (
                    f"{shlex.quote(hpc.python_bin)} -m src.polybench_pcce.worker "
                    f"--config {shlex.quote(hpc.worker_config_path)} "
                    '--task-manifest "${TASK_MANIFEST}" '
                    '--output "${OUTPUT_JSON}" '
                    '--attempt-dir "${ATTEMPT_DIR}" '
                    '--checkpoint-dir "${CHECKPOINT_DIR}" '
                    '--attempt "${ATTEMPT}"'
                ),
            ]
        )
        + "\n"
    )


class PolyBenchPCCEHPCExecutor:
    def __init__(self, config: PolyBenchPCCEConfig) -> None:
        self.config = config
        self.runtime = SlurmTaskBatch(config.hpc)

    def run_pc(self, assignments: Sequence[PCReviewAssignment]) -> list[dict[str, Any]]:
        semantic = pcce_semantic_sha256(self.config)
        fingerprint = _stable(
            {
                "schema": 1,
                "phase": "pc",
                "semantic": semantic,
                "assignments": [
                    {
                        "instance_id": item.case.instance_id,
                        "review_index": item.review_index,
                        "rejection_count": item.rejection_count,
                        "input_plan_sha256": text_sha256(item.input_plan),
                        "previous_feedback_sha256": text_sha256(item.previous_feedback),
                    }
                    for item in assignments
                ],
            }
        )
        review_index = assignments[0].review_index
        if any(item.review_index != review_index for item in assignments):
            raise ValueError("one PC wave must contain one review index")
        batch_dir = (
            self.config.run_dir
            / "hpc_tasks"
            / "pc"
            / f"review_{review_index:02d}"
            / fingerprint
        )
        guideline_artifact = batch_dir / "guideline.md"
        guideline = self.config.guideline_path.read_text(encoding="utf-8")
        if (
            guideline_artifact.is_file()
            and guideline_artifact.read_text(encoding="utf-8") != guideline
        ):
            raise ValueError("PCCE guideline artifact mismatch")
        guideline_artifact.parent.mkdir(parents=True, exist_ok=True)
        guideline_artifact.write_text(guideline, encoding="utf-8")
        payloads = [
            {
                "review_index": item.review_index,
                "rejection_count": item.rejection_count,
                "input_plan": item.input_plan,
                "previous_feedback": item.previous_feedback,
                "guideline_relpath": str(
                    guideline_artifact.relative_to(self.config.run_dir)
                ),
                "guideline_sha256": text_sha256(guideline),
            }
            for item in assignments
        ]
        return self._run_batch(
            "pc", batch_dir, fingerprint, [item.case for item in assignments], payloads
        )

    def run_ce(self, assignments: Sequence[CEAssignment]) -> list[dict[str, Any]]:
        fingerprint = _stable(
            {
                "schema": 1,
                "phase": "ce",
                "semantic": pcce_semantic_sha256(self.config),
                "assignments": [
                    {
                        "instance_id": item.case.instance_id,
                        "accepted_plan_sha256": text_sha256(item.accepted_plan),
                    }
                    for item in assignments
                ],
            }
        )
        batch_dir = self.config.run_dir / "hpc_tasks" / "ce" / fingerprint
        payloads = [
            {
                "accepted_review_relpath": str(
                    item.accepted_review_path.relative_to(self.config.run_dir)
                ),
                "accepted_plan": item.accepted_plan,
                "accepted_plan_sha256": text_sha256(item.accepted_plan),
            }
            for item in assignments
        ]
        return self._run_batch(
            "ce", batch_dir, fingerprint, [item.case for item in assignments], payloads
        )

    def _run_batch(
        self,
        phase: str,
        batch_dir: Path,
        fingerprint: str,
        cases: Sequence[PCCECase],
        extras: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        tasks: list[TaskFiles] = []
        for index, (case, extra) in enumerate(zip(cases, extras, strict=True)):
            manifest = batch_dir / "tasks" / f"task_{index:04d}.json"
            output = batch_dir / "outputs" / f"task_{index:04d}.json"
            payload = {
                "schema_version": 1,
                "mode": "polybench_pcce",
                "phase": phase,
                "fingerprint": fingerprint,
                "task_index": index,
                "instance_id": case.instance_id,
                "case": _case_dict(case),
                **extra,
            }
            if (
                manifest.is_file()
                and json.loads(manifest.read_text(encoding="utf-8")) != payload
            ):
                raise ValueError(f"PCCE {phase} task manifest mismatch")
            if not manifest.exists():
                atomic_json(manifest, payload)
            tasks.append(
                TaskFiles(
                    index,
                    case.instance_id,
                    manifest,
                    output,
                    batch_dir / "attempts" / f"task_{index:04d}",
                )
            )
        atomic_json(
            batch_dir / "manifest.json",
            {
                "schema_version": 1,
                "mode": "polybench_pcce_batch",
                "phase": phase,
                "fingerprint": fingerprint,
                "task_count": len(tasks),
                "instance_ids": [case.instance_id for case in cases],
            },
        )

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"{phase}_array_attempt_{attempt:02d}.sbatch"
            path.write_text(
                build_array_script(
                    config=self.config,
                    batch_dir=batch_dir,
                    indices=indices,
                    attempt=attempt,
                    phase=phase,
                ),
                encoding="utf-8",
            )
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if (
                value.get("fingerprint") != fingerprint
                or value.get("instance_id") != task.instance_id
            ):
                raise ValueError("PCCE worker output identity mismatch")
            if value.get("phase") != phase:
                raise ValueError("PCCE worker output phase mismatch")
            if phase == "pc":
                checker = value.get("checker_output")
                if (
                    value.get("pc_status") != "completed"
                    or not isinstance(checker, dict)
                    or not isinstance(checker.get("should_proceed"), bool)
                ):
                    raise ValueError("PCCE PC output lacks a valid decision")
            elif value.get("pcce_status") != "completed":
                raise ValueError("PCCE CE output is incomplete")

        try:
            return self.runtime.run(
                batch_dir=batch_dir,
                fingerprint=fingerprint,
                tasks=tasks,
                job_name=lambda attempt: (
                    f"{self.config.hpc.job_name_prefix}-{phase}-{batch_dir.name[:10]}-a{attempt}"
                ),
                write_script=write_script,
                validate_output=validate,
            )
        except TaskAttemptsExhausted:
            return self._collect_exhausted(phase, batch_dir, fingerprint, tasks)

    def _collect_exhausted(
        self, phase: str, batch_dir: Path, fingerprint: str, tasks: Sequence[TaskFiles]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for task in tasks:
            value: dict[str, Any] = {}
            if task.output_path.is_file():
                loaded = json.loads(task.output_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    value = loaded
            if value.get("status") == "completed":
                results.append(value)
            else:
                results.append(
                    {
                        "schema_version": 1,
                        "status": "incomplete",
                        "mode": "polybench_pcce",
                        "phase": phase,
                        "fingerprint": fingerprint,
                        "task_index": task.index,
                        "instance_id": task.instance_id,
                        "task_attempts_exhausted": self.config.hpc.max_task_attempts,
                        "last_worker_output": value or None,
                        "evidence_root": str(task.attempts_dir),
                    }
                )
        atomic_json(
            batch_dir / "exhausted_collection.json",
            {"schema_version": 1, "results": results},
        )
        return results
