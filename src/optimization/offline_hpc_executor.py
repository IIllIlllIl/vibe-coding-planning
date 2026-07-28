"""Offline GEPA task definitions on the shared Slurm runtime."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Sequence

from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.checker import validate_checker_output
from src.optimization.config import OptimizationConfig
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskFiles,
    atomic_json,
)
from src.optimization.models import CheckerOutput, GEPACase


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def offline_checker_semantic_sha256(config: OptimizationConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    source_paths = [
        root / "src" / "agents" / "_deps.py",
        root / "src" / "environment" / "apptainer_env.py",
        root / "src" / "optimization" / "checker.py",
        root / "src" / "optimization" / "offline_checker_worker.py",
    ]
    return _stable_sha256(
        {
            "schema": 1,
            "source": {
                str(path.relative_to(root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in source_paths
            },
            "checker": asdict(config.checker),
            "container": {
                **asdict(config.container),
                "sif_cache_dir": str(config.container.sif_cache_dir),
            },
            "checker_prompt": config.checker_prompt,
            "checker_instance_template": config.checker_instance_template,
        }
    )


def offline_evaluation_fingerprint(
    config: OptimizationConfig,
    *,
    batch: Sequence[GEPACase],
    rules: str,
    capture_traces: bool,
) -> str:
    """Identify predictions without placing historical labels in worker input."""
    return _stable_sha256(
        {
            "schema": 1,
            "checker_semantic_sha256": offline_checker_semantic_sha256(config),
            "candidate_sha256": text_sha256(rules),
            "capture_traces": capture_traces,
            "cases": [
                {
                    "instance_id": case.instance_id,
                    "split": case.split,
                    "checker_payload": case.checker_payload(),
                }
                for case in batch
            ],
        }
    )


def build_offline_checker_array_script(
    *,
    config: OptimizationConfig,
    batch_dir: Path,
    task_indices: Sequence[int],
    attempt: int,
) -> str:
    hpc = config.hpc
    index_spec = ",".join(str(index) for index in task_indices)
    array_spec = f"{index_spec}%{hpc.max_running_array_tasks}"
    config_path = hpc.worker_config_path
    if not config_path:
        raise ValueError("hpc.worker_config_path is required")
    job_name = (
        f"{hpc.job_name_prefix}-checker-{batch_dir.name[:12]}-a{attempt}"
    )
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}",
        f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}",
        f"#SBATCH --time={hpc.time}",
        f"#SBATCH --array={array_spec}",
        "#SBATCH --output=%x-%A_%a.out",
        "#SBATCH --error=%x-%A_%a.err",
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
        f"{shlex.quote(hpc.python_bin)} "
        "-m src.optimization.offline_checker_worker "
        f"--config {shlex.quote(config_path)} "
        '--task-manifest "${TASK_MANIFEST}" '
        '--output "${OUTPUT_JSON}" '
        '--attempt-dir "${ATTEMPT_DIR}"',
    ]
    return "\n".join(lines) + "\n"


class HPCSlurmOfflineCheckerExecutor:
    """Evaluate an Offline Checker batch as independent Slurm tasks."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.root = config.run_dir / "hpc_tasks" / "checker"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = SlurmTaskBatch(config.hpc)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    def evaluate(
        self,
        batch: list[GEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[CheckerOutput]:
        fingerprint = offline_evaluation_fingerprint(
            self.config,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
        )
        batch_dir = self.root / fingerprint
        tasks = self._prepare(
            batch_dir,
            fingerprint=fingerprint,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
        )

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"checker_array_attempt_{attempt:02d}.sbatch"
            path.write_text(
                build_offline_checker_array_script(
                    config=self.config,
                    batch_dir=batch_dir,
                    task_indices=indices,
                    attempt=attempt,
                ),
                encoding="utf-8",
            )
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint:
                raise ValueError("Offline Checker output fingerprint mismatch")
            if value.get("instance_id") != task.instance_id:
                raise ValueError("Offline Checker output instance mismatch")
            validate_checker_output(dict(value["checker_output"]))

        outputs = self.runtime.run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: (
                f"{self.config.hpc.job_name_prefix}-checker-"
                f"{batch_dir.name[:12]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
        self.audit.write(
            "offline_hpc_checker_batch_completed",
            batch_dir=str(batch_dir),
            fingerprint=fingerprint,
            instance_ids=[case.instance_id for case in batch],
        )
        return [
            CheckerOutput(
                parsed.predicted_resolved,
                parsed.decision_reason,
                parsed.repository_evidence,
                tuple(dict(value["checker_output"]).get("trajectory", [])),
            )
            for value in outputs
            for parsed in [validate_checker_output(dict(value["checker_output"]))]
        ]

    @staticmethod
    def _prepare(
        batch_dir: Path,
        *,
        fingerprint: str,
        batch: Sequence[GEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[TaskFiles]:
        tasks_dir = batch_dir / "tasks"
        outputs_dir = batch_dir / "outputs"
        attempts_dir = batch_dir / "attempts"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir.mkdir(exist_ok=True)
        attempts_dir.mkdir(exist_ok=True)
        rules_path = batch_dir / "candidate_rules.txt"
        if not rules_path.exists():
            rules_path.write_text(rules, encoding="utf-8")
        elif rules_path.read_text(encoding="utf-8") != rules:
            raise ValueError("Offline Checker batch candidate rules mismatch")
        tasks: list[TaskFiles] = []
        for index, case in enumerate(batch):
            manifest_path = tasks_dir / f"task_{index:04d}.json"
            payload = {
                "schema_version": 1,
                "mode": "offline_checker",
                "fingerprint": fingerprint,
                "index": index,
                "instance_id": case.instance_id,
                "split": case.split,
                "capture_traces": capture_traces,
                "candidate_sha256": text_sha256(rules),
                "rules_path": str(rules_path),
                "checker_payload": case.checker_payload(),
            }
            if manifest_path.exists():
                if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                    raise ValueError("Offline Checker task manifest mismatch")
            else:
                atomic_json(manifest_path, payload)
            tasks.append(
                TaskFiles(
                    index=index,
                    instance_id=case.instance_id,
                    manifest_path=manifest_path,
                    output_path=outputs_dir / f"task_{index:04d}.json",
                    attempts_dir=attempts_dir / f"task_{index:04d}",
                )
            )
        atomic_json(
            batch_dir / "manifest.json",
            {
                "schema_version": 1,
                "mode": "offline_checker_batch",
                "fingerprint": fingerprint,
                "candidate_sha256": text_sha256(rules),
                "capture_traces": capture_traces,
                "instance_ids": [case.instance_id for case in batch],
                "contains_historical_labels": False,
            },
        )
        return tasks
