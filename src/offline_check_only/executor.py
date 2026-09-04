"""One combined Slurm batch for fixed-guideline Checker-only evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Sequence

from src.offline_check_only.config import CheckOnlyConfig
from src.offline_check_only.dataset import CheckOnlyCase
from src.optimization.audit import text_sha256
from src.optimization.checker import validate_checker_output
from src.optimization.hpc.task_batch import TaskAttemptsExhausted, TaskFiles, atomic_json
from src.optimization.models import CheckerIncompleteOutput, CheckerResult
from src.optimization.offline_hpc_executor import (
    HPCSlurmOfflineCheckerExecutor,
    offline_checker_semantic_sha256,
)


@dataclass(frozen=True)
class CheckerAssignment:
    case: CheckOnlyCase
    guideline_label: str
    guideline: str

    @property
    def evaluation_id(self) -> str:
        return f"{self.guideline_label}::{self.case.instance_id}"


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def check_only_fingerprint(
    config: CheckOnlyConfig,
    assignments: Sequence[CheckerAssignment],
) -> str:
    root = Path(__file__).resolve().parents[2]
    additive_sources = [
        root / "src" / "offline_check_only" / "executor.py",
        root / "src" / "offline_check_only" / "worker.py",
        root / "src" / "offline_check_only" / "dataset.py",
    ]
    return _stable_sha256(
        {
            "schema": 1,
            "checker_semantic_sha256": offline_checker_semantic_sha256(config.runtime),
            "additive_source_sha256": {
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in additive_sources
            },
            "capture_traces": True,
            "assignments": [
                {
                    "evaluation_id": item.evaluation_id,
                    "guideline_sha256": text_sha256(item.guideline),
                    "checker_payload": item.case.checker_payload(),
                }
                for item in assignments
            ],
        }
    )


def build_array_script(
    *,
    config: CheckOnlyConfig,
    batch_dir: Path,
    task_indices: Sequence[int],
    attempt: int,
) -> str:
    hpc = config.runtime.hpc
    logs = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    logs.mkdir(parents=True, exist_ok=True)
    indices = ",".join(str(index) for index in task_indices)
    job_name = f"{hpc.job_name_prefix}-check-only-{batch_dir.name[:10]}-a{attempt}"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}",
        f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}",
        f"#SBATCH --time={hpc.time}",
        f"#SBATCH --array={indices}",
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
        'mkdir -p "${ATTEMPT_DIR}"',
    ]
    command = (
        f"{shlex.quote(hpc.python_bin)} -m src.offline_check_only.worker "
        f"--config {shlex.quote(str(config.config_path))} "
        '--task-manifest "${TASK_MANIFEST}" '
        '--output "${OUTPUT_JSON}" '
        '--attempt-dir "${ATTEMPT_DIR}"'
    )
    if attempt > 1:
        previous = batch_dir / "failed_outputs" / f"attempt_{attempt - 1:02d}"
        lines.append(f"PREVIOUS_OUTPUT={shlex.quote(str(previous))}/task_${{TASK_ID}}.json")
        command += ' --previous-output "${PREVIOUS_OUTPUT}"'
    lines.append(command)
    return "\n".join(lines) + "\n"


class CheckOnlyHPCExecutor(HPCSlurmOfflineCheckerExecutor):
    """Reuse current Offline task retries and timeout recovery without GEPA."""

    def __init__(self, config: CheckOnlyConfig) -> None:
        super().__init__(config.runtime)
        self.check_only_config = config
        self.root = config.run_dir / "hpc_tasks" / "checker"
        self.root.mkdir(parents=True, exist_ok=True)

    def evaluate_assignments(
        self,
        assignments: list[CheckerAssignment],
    ) -> list[CheckerResult | CheckerIncompleteOutput]:
        fingerprint = check_only_fingerprint(self.check_only_config, assignments)
        batch_dir = self.root / fingerprint
        tasks = self._prepare_assignments(batch_dir, fingerprint, assignments)

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"checker_array_attempt_{attempt:02d}.sbatch"
            path.write_text(
                build_array_script(
                    config=self.check_only_config,
                    batch_dir=batch_dir,
                    task_indices=indices,
                    attempt=attempt,
                ),
                encoding="utf-8",
            )
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint:
                raise ValueError("check-only output fingerprint mismatch")
            if value.get("instance_id") != task.instance_id:
                raise ValueError("check-only evaluation identity mismatch")
            validate_checker_output(dict(value["checker_output"]))

        try:
            outputs = self.runtime.run(
                batch_dir=batch_dir,
                fingerprint=fingerprint,
                tasks=tasks,
                job_name=lambda attempt: (
                    f"{self.config.hpc.job_name_prefix}-check-only-"
                    f"{batch_dir.name[:10]}-a{attempt}"
                ),
                write_script=write_script,
                validate_output=validate,
            )
        except TaskAttemptsExhausted as exc:
            try:
                return self._recover_stability_incomplete(
                    batch_dir=batch_dir,
                    fingerprint=fingerprint,
                    tasks=tasks,
                )
            except (OSError, ValueError, KeyError, TypeError) as recovery_error:
                raise exc from recovery_error
        return [self._completed_result(value) for value in outputs]

    @staticmethod
    def _prepare_assignments(
        batch_dir: Path,
        fingerprint: str,
        assignments: Sequence[CheckerAssignment],
    ) -> list[TaskFiles]:
        tasks_dir = batch_dir / "tasks"
        outputs_dir = batch_dir / "outputs"
        attempts_root = batch_dir / "attempts"
        guidelines_dir = batch_dir / "guidelines"
        for path in (tasks_dir, outputs_dir, attempts_root, guidelines_dir):
            path.mkdir(parents=True, exist_ok=True)
        guideline_paths: dict[str, Path] = {}
        for assignment in assignments:
            path = guidelines_dir / f"{assignment.guideline_label}.md"
            if path.is_file() and path.read_text(encoding="utf-8") != assignment.guideline:
                raise ValueError("check-only guideline artifact mismatch")
            if not path.exists():
                path.write_text(assignment.guideline, encoding="utf-8")
            guideline_paths[assignment.guideline_label] = path

        tasks: list[TaskFiles] = []
        for index, assignment in enumerate(assignments):
            manifest_path = tasks_dir / f"task_{index:04d}.json"
            payload = {
                "schema_version": 1,
                "mode": "offline_check_only",
                "fingerprint": fingerprint,
                "index": index,
                "instance_id": assignment.evaluation_id,
                "benchmark_instance_id": assignment.case.instance_id,
                "split": assignment.case.split,
                "guideline_label": assignment.guideline_label,
                "candidate_sha256": text_sha256(assignment.guideline),
                "rules_path": str(guideline_paths[assignment.guideline_label]),
                "capture_traces": True,
                "checker_payload": assignment.case.checker_payload(),
            }
            if manifest_path.is_file():
                if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                    raise ValueError("check-only task manifest mismatch")
            else:
                atomic_json(manifest_path, payload)
            tasks.append(
                TaskFiles(
                    index=index,
                    instance_id=assignment.evaluation_id,
                    manifest_path=manifest_path,
                    output_path=outputs_dir / f"task_{index:04d}.json",
                    attempts_dir=attempts_root / f"task_{index:04d}",
                )
            )
        atomic_json(
            batch_dir / "manifest.json",
            {
                "schema_version": 1,
                "mode": "offline_check_only_batch",
                "fingerprint": fingerprint,
                "tasks": len(assignments),
                "evaluation_ids": [item.evaluation_id for item in assignments],
                "contains_historical_labels": False,
            },
        )
        return tasks
