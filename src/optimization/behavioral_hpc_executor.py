"""Label-free Slurm execution for Behavioral Checker tasks."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Sequence

from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.behavioral_models import (
    BehavioralCheckerOutput,
    BehavioralGEPACase,
)
from src.optimization.behavioral_runtime import validate_behavioral_checker_output
from src.optimization.config import OptimizationConfig
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskFiles,
    atomic_json,
)


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


def behavioral_checker_semantic_sha256(config: OptimizationConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "src/agents/_deps.py",
        root / "src/optimization/behavioral_models.py",
        root / "src/optimization/behavioral_repository.py",
        root / "src/optimization/behavioral_runtime.py",
        root / "src/optimization/behavioral_checker_worker.py",
        root / "src/optimization/behavioral_hpc_executor.py",
    ]
    return _stable_sha256(
        {
            "schema": 1,
            "task_semantics": config.task.semantics,
            "source": {
                str(path.relative_to(root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in paths
            },
            "checker": asdict(config.checker),
            "repository_backend": asdict(config.behavioral_repository),
            "checker_prompt": config.checker_prompt,
            "checker_instance_template": config.checker_instance_template,
        }
    )


def behavioral_evaluation_fingerprint(
    config: OptimizationConfig,
    *,
    batch: Sequence[BehavioralGEPACase],
    rules: str,
    capture_traces: bool,
) -> str:
    return _stable_sha256(
        {
            "schema": 1,
            "checker_semantic_sha256": behavioral_checker_semantic_sha256(config),
            "candidate_sha256": text_sha256(rules),
            "capture_traces": capture_traces,
            "cases": [
                {
                    "instance_id": case.instance_id,
                    "split": case.split,
                    "repetition_index": case.repetition_index,
                    "worker_payload": case.worker_payload(
                        config.behavioral_repository.repositories_root
                    ),
                }
                for case in batch
            ],
        }
    )


def build_behavioral_checker_array_script(
    *,
    config: OptimizationConfig,
    batch_dir: Path,
    task_indices: Sequence[int],
    attempt: int,
) -> str:
    hpc = config.hpc
    index_spec = ",".join(str(index) for index in task_indices)
    log_dir = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = hpc.worker_config_path
    if not config_path:
        raise ValueError("hpc.worker_config_path is required")
    job_name = f"{hpc.job_name_prefix}-checker-{batch_dir.name[:12]}-a{attempt}"
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
                "#SBATCH --export=ALL",
                f"#SBATCH --output={log_dir}/%x-%A_%a.out",
                f"#SBATCH --error={log_dir}/%x-%A_%a.err",
                "set -euo pipefail",
                "set +x",
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
                "-m src.optimization.behavioral_checker_worker "
                f"--config {shlex.quote(config_path)} "
                '--task-manifest "${TASK_MANIFEST}" '
                '--output "${OUTPUT_JSON}" '
                '--attempt-dir "${ATTEMPT_DIR}"',
            ]
        )
        + "\n"
    )


class HPCSlurmBehavioralCheckerExecutor:
    """Evaluate a Behavioral batch as independent Slurm Agent tasks."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.root = config.run_dir / "hpc_tasks" / "checker"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = SlurmTaskBatch(config.hpc)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    def evaluate(
        self,
        batch: list[BehavioralGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[BehavioralCheckerOutput]:
        fingerprint = behavioral_evaluation_fingerprint(
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
                build_behavioral_checker_array_script(
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
                raise ValueError("Behavioral Checker output fingerprint mismatch")
            if value.get("instance_id") != task.instance_id:
                raise ValueError("Behavioral Checker output instance mismatch")
            manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
            if value.get("repetition_index") != manifest.get("repetition_index"):
                raise ValueError("Behavioral Checker repetition mismatch")
            raw = dict(value["checker_output"])
            validate_behavioral_checker_output(
                {
                    key: raw[key]
                    for key in (
                        "predicted_accept",
                        "decision_reason",
                        "repository_evidence",
                    )
                }
            )
            if not isinstance(raw.get("trajectory"), list):
                raise ValueError("Behavioral Checker trajectory is missing")

        outputs = self.runtime.run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: (
                f"{self.config.hpc.job_name_prefix}-checker-"
                f"{fingerprint[:12]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
        self.audit.write(
            "behavioral_hpc_checker_batch_completed",
            fingerprint=fingerprint,
            instance_ids=[case.instance_id for case in batch],
        )
        results = []
        for value in outputs:
            raw = dict(value["checker_output"])
            parsed = validate_behavioral_checker_output(
                {
                    key: raw[key]
                    for key in (
                        "predicted_accept",
                        "decision_reason",
                        "repository_evidence",
                    )
                }
            )
            results.append(
                BehavioralCheckerOutput(
                    predicted_accept=parsed.predicted_accept,
                    decision_reason=parsed.decision_reason,
                    repository_evidence=parsed.repository_evidence,
                    trajectory=tuple(raw.get("trajectory", [])),
                )
            )
        return results

    def _prepare(
        self,
        batch_dir: Path,
        *,
        fingerprint: str,
        batch: Sequence[BehavioralGEPACase],
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
        if rules_path.exists() and rules_path.read_text(encoding="utf-8") != rules:
            raise ValueError("Behavioral Checker candidate rules mismatch")
        if not rules_path.exists():
            rules_path.write_text(rules, encoding="utf-8")
        tasks = []
        for index, case in enumerate(batch):
            manifest_path = tasks_dir / f"task_{index:04d}.json"
            payload = {
                "schema_version": 1,
                "mode": "behavioral_checker",
                "fingerprint": fingerprint,
                "index": index,
                "instance_id": case.instance_id,
                "split": case.split,
                "capture_traces": capture_traces,
                "candidate_sha256": text_sha256(rules),
                "rules_path": "../candidate_rules.txt",
                "worker_payload": case.worker_payload(
                    self.config.behavioral_repository.repositories_root
                ),
            }
            if case.repetition_index is not None:
                payload["repetition_index"] = case.repetition_index
            if manifest_path.exists():
                if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                    raise ValueError("Behavioral Checker task manifest mismatch")
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
                "mode": "behavioral_checker_batch",
                "fingerprint": fingerprint,
                "candidate_sha256": text_sha256(rules),
                "capture_traces": capture_traces,
                "instance_ids": [case.instance_id for case in batch],
                "contains_observed_decision": False,
                "contains_post_boundary_evidence": False,
            },
        )
        return tasks
