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
    TaskAttemptsExhausted,
    TaskFiles,
    atomic_json,
)
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerResult,
    CheckerTimeoutOutput,
    GEPACase,
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


def offline_checker_semantic_sha256(config: OptimizationConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    source_paths = [
        root / "src" / "agents" / "_deps.py",
        root / "src" / "environment" / "apptainer_env.py",
        root / "src" / "environment" / "repository_baseline.py",
        root / "src" / "optimization" / "checker.py",
        root / "src" / "optimization" / "offline_checker_worker.py",
        root / "src" / "optimization" / "offline_hpc_executor.py",
    ]
    return _stable_sha256(
        {
            "schema": 4,
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
            "retry_policy": {
                "kind": (
                    "fresh_agent; validator feedback only for output-contract "
                    "failures; controller-classified evidenced Slurm timeout "
                    "exhaustion scores zero"
                ),
                "max_task_attempts": config.hpc.max_task_attempts,
            },
        }
    )


def offline_evaluation_fingerprint(
    config: OptimizationConfig,
    *,
    batch: Sequence[GEPACase],
    rules: str,
    capture_traces: bool,
    evaluation_tag: str | None = None,
) -> str:
    """Identify predictions without placing historical labels in worker input."""
    payload = {
        "schema": 1,
        "checker_semantic_sha256": offline_checker_semantic_sha256(config),
        "candidate_sha256": text_sha256(rules),
        "capture_traces": capture_traces,
        "cases": [
            {
                "instance_id": case.instance_id,
                "split": case.split,
                **(
                    {"repetition_index": case.repetition_index}
                    if case.repetition_index is not None
                    else {}
                ),
                "checker_payload": case.checker_payload(),
            }
            for case in batch
        ],
    }
    if evaluation_tag is not None:
        payload["evaluation_tag"] = evaluation_tag
    return _stable_sha256(payload)


def build_offline_checker_array_script(
    *,
    config: OptimizationConfig,
    batch_dir: Path,
    task_indices: Sequence[int],
    attempt: int,
) -> str:
    hpc = config.hpc
    slurm_log_dir = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    slurm_log_dir.mkdir(parents=True, exist_ok=True)
    index_spec = ",".join(str(index) for index in task_indices)
    config_path = hpc.worker_config_path
    if not config_path:
        raise ValueError("hpc.worker_config_path is required")
    job_name = f"{hpc.job_name_prefix}-checker-{batch_dir.name[:12]}-a{attempt}"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}",
        f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}",
        f"#SBATCH --time={hpc.time}",
        f"#SBATCH --array={index_spec}",
        f"#SBATCH --output={slurm_log_dir}/%x-%A_%a.out",
        f"#SBATCH --error={slurm_log_dir}/%x-%A_%a.err",
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
    worker_command = (
        f"{shlex.quote(hpc.python_bin)} "
        "-m src.optimization.offline_checker_worker "
        f"--config {shlex.quote(config_path)} "
        '--task-manifest "${TASK_MANIFEST}" '
        '--output "${OUTPUT_JSON}" '
        '--attempt-dir "${ATTEMPT_DIR}"'
    )
    if attempt > 1:
        previous_dir = batch_dir / "failed_outputs" / f"attempt_{attempt - 1:02d}"
        lines.append(
            f"PREVIOUS_OUTPUT={shlex.quote(str(previous_dir))}/task_${{TASK_ID}}.json"
        )
        worker_command += ' --previous-output "${PREVIOUS_OUTPUT}"'
    lines.append(worker_command)
    return "\n".join(lines) + "\n"


class HPCSlurmOfflineCheckerExecutor:
    """Evaluate an Offline Checker batch as independent Slurm tasks."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.root = config.run_dir / "hpc_tasks" / "checker"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = SlurmTaskBatch(config.hpc)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    @staticmethod
    def _validate_task_identity(
        task: TaskFiles,
        value: dict[str, Any],
        *,
        fingerprint: str,
    ) -> None:
        if value.get("fingerprint") != fingerprint:
            raise ValueError("Offline Checker output fingerprint mismatch")
        if value.get("instance_id") != task.instance_id:
            raise ValueError("Offline Checker output instance mismatch")
        manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
        if value.get("repetition_index") != manifest.get("repetition_index"):
            raise ValueError("Offline Checker output repetition mismatch")

    def evaluate(
        self,
        batch: list[GEPACase],
        rules: str,
        capture_traces: bool,
        *,
        evaluation_tag: str | None = None,
        allow_incomplete: bool = False,
    ) -> list[CheckerResult | CheckerIncompleteOutput]:
        fingerprint = offline_evaluation_fingerprint(
            self.config,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
            evaluation_tag=evaluation_tag,
        )
        batch_dir = self.root / fingerprint
        tasks = self._prepare(
            batch_dir,
            fingerprint=fingerprint,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
            evaluation_tag=evaluation_tag,
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
            self._validate_task_identity(
                task,
                value,
                fingerprint=fingerprint,
            )
            validate_checker_output(dict(value["checker_output"]))

        try:
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
        except TaskAttemptsExhausted as exc:
            if allow_incomplete:
                return self._recover_stability_incomplete(
                    batch_dir=batch_dir,
                    fingerprint=fingerprint,
                    tasks=tasks,
                )
            try:
                results = self._recover_evidenced_timeouts(
                    batch_dir=batch_dir,
                    fingerprint=fingerprint,
                    tasks=tasks,
                )
            except (OSError, ValueError, KeyError, TypeError) as recovery_error:
                raise exc from recovery_error
            self.audit.write(
                "offline_hpc_checker_timeout_exhaustion_scored",
                batch_dir=str(batch_dir),
                fingerprint=fingerprint,
                timeout_instance_ids=[
                    task.instance_id
                    for task, result in zip(tasks, results, strict=True)
                    if isinstance(result, CheckerTimeoutOutput)
                ],
                attempts=self.config.hpc.max_task_attempts,
                score=0.0,
            )
            return results
        self.audit.write(
            "offline_hpc_checker_batch_completed",
            batch_dir=str(batch_dir),
            fingerprint=fingerprint,
            evaluation_tag=evaluation_tag,
            instance_ids=[case.instance_id for case in batch],
            repetition_indices=[case.repetition_index for case in batch],
        )
        return [self._completed_result(value) for value in outputs]

    def _recover_stability_incomplete(
        self,
        *,
        batch_dir: Path,
        fingerprint: str,
        tasks: Sequence[TaskFiles],
    ) -> list[CheckerResult | CheckerIncompleteOutput]:
        """Collect valid predictions and classify exhausted diagnostic tasks."""
        results: list[CheckerResult | CheckerIncompleteOutput] = []
        attempt = self.config.hpc.max_task_attempts
        for task in tasks:
            if task.output_path.is_file():
                value = json.loads(task.output_path.read_text(encoding="utf-8"))
                if value.get("status") == "completed":
                    self._validate_task_identity(
                        task,
                        value,
                        fingerprint=fingerprint,
                    )
                    results.append(self._completed_result(value))
                    continue
            else:
                value = {}

            slurm_path = (
                task.attempts_dir / f"attempt_{attempt:02d}" / "slurm_status.json"
            )
            slurm = (
                json.loads(slurm_path.read_text(encoding="utf-8"))
                if slurm_path.is_file()
                else {}
            )
            terminal_state = slurm.get("state")
            failure_kind = str(value.get("failure_kind") or "task_exhausted")
            failure_category = str(
                value.get("failure_category")
                or ("timeout" if terminal_state == "TIMEOUT" else "infrastructure")
            )
            results.append(
                CheckerIncompleteOutput(
                    failure_kind=failure_kind,
                    failure_category=failure_category,
                    terminal_state=(
                        str(terminal_state) if terminal_state is not None else None
                    ),
                    attempts=attempt,
                    error=str(value.get("error", "")),
                )
            )
        self.audit.write(
            "offline_hpc_checker_stability_incomplete_collected",
            batch_dir=str(batch_dir),
            fingerprint=fingerprint,
            incomplete_instance_ids=[
                task.instance_id
                for task, result in zip(tasks, results, strict=True)
                if isinstance(result, CheckerIncompleteOutput)
            ],
        )
        return results

    @staticmethod
    def _completed_result(value: dict[str, Any]) -> CheckerOutput:
        checker_output = dict(value["checker_output"])
        parsed = validate_checker_output(checker_output)
        return CheckerOutput(
            parsed.predicted_resolved,
            parsed.decision_reason,
            parsed.repository_evidence,
            tuple(checker_output.get("trajectory", [])),
        )

    def _recover_evidenced_timeouts(
        self,
        *,
        batch_dir: Path,
        fingerprint: str,
        tasks: Sequence[TaskFiles],
    ) -> list[CheckerResult]:
        """Classify exhausted Slurm timeouts using durable worker evidence."""
        results: list[CheckerResult] = []
        max_attempts = self.config.hpc.max_task_attempts
        for task in tasks:
            if task.output_path.is_file():
                current = json.loads(task.output_path.read_text(encoding="utf-8"))
                if current.get("status") == "completed":
                    self._validate_task_identity(
                        task,
                        current,
                        fingerprint=fingerprint,
                    )
                    results.append(self._completed_result(current))
                    continue

            attempt_failures = []
            trajectories = []
            elapsed_seconds = []
            for attempt in range(1, max_attempts + 1):
                output_path = (
                    task.output_path
                    if attempt == max_attempts
                    else batch_dir
                    / "failed_outputs"
                    / f"attempt_{attempt:02d}"
                    / task.output_path.name
                )
                failure = (
                    json.loads(output_path.read_text(encoding="utf-8"))
                    if output_path.is_file()
                    else None
                )
                explicit_timeout = bool(
                    failure
                    and failure.get("status") == "agent_failed"
                    and failure.get("failure_kind") == "checker_agent_timeout"
                    and failure.get("fingerprint") == fingerprint
                    and failure.get("instance_id") == task.instance_id
                )
                if explicit_timeout:
                    manifest = json.loads(
                        task.manifest_path.read_text(encoding="utf-8")
                    )
                    if failure.get("repetition_index") != manifest.get(
                        "repetition_index"
                    ):
                        raise ValueError(
                            "Checker timeout repetition identity mismatch"
                        )
                slurm_path = (
                    task.attempts_dir
                    / f"attempt_{attempt:02d}"
                    / "slurm_status.json"
                )
                slurm = (
                    json.loads(slurm_path.read_text(encoding="utf-8"))
                    if slurm_path.is_file()
                    else {}
                )
                if slurm and (
                    slurm.get("instance_id") != task.instance_id
                    or slurm.get("task_index") != task.index
                ):
                    raise ValueError("Checker Slurm status identity mismatch")
                slurm_timeout = slurm.get("state") == "TIMEOUT"
                if not explicit_timeout and not slurm_timeout:
                    raise ValueError(
                        "Checker exhaustion contains a non-timeout attempt"
                    )
                if failure is not None and not explicit_timeout:
                    raise ValueError(
                        "Checker timeout attempt contains conflicting worker output"
                    )
                attempt_failures.append(failure or slurm)
                observed_elapsed = slurm.get("elapsed_seconds")
                if isinstance(observed_elapsed, int):
                    elapsed_seconds.append(observed_elapsed)
                trajectory_path = (
                    task.attempts_dir
                    / f"attempt_{attempt:02d}"
                    / "checker_trajectory.json"
                )
                if trajectory_path.is_file():
                    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
                    messages = trajectory.get("messages")
                    if not isinstance(messages, list):
                        raise ValueError("Checker timeout trajectory must be a list")
                    trajectory = tuple(messages)
                else:
                    journal_path = (
                        task.attempts_dir
                        / f"attempt_{attempt:02d}"
                        / "checker_trajectory.jsonl"
                    )
                    if not journal_path.is_file():
                        raise ValueError(
                            "Checker timeout attempt has no durable trajectory"
                        )
                    trajectory = tuple(
                        json.loads(line)
                        for line in journal_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                        if line.strip()
                    )
                if not trajectory or not any(
                    message.get("role") == "assistant"
                    for message in trajectory
                    if isinstance(message, dict)
                ):
                    raise ValueError(
                        "Checker timeout trajectory does not show Agent reasoning"
                    )
                trajectories.append(trajectory)
            if len(attempt_failures) != max_attempts:
                raise ValueError("Checker timeout attempt evidence is incomplete")
            results.append(
                CheckerTimeoutOutput(
                    attempts=max_attempts,
                    timeout_seconds=(
                        elapsed_seconds[-1]
                        if elapsed_seconds
                        else self.config.checker.agent_timeout_seconds
                    ),
                    trajectories=tuple(trajectories),
                )
            )
        return results

    @staticmethod
    def _prepare(
        batch_dir: Path,
        *,
        fingerprint: str,
        batch: Sequence[GEPACase],
        rules: str,
        capture_traces: bool,
        evaluation_tag: str | None = None,
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
                # Worker input paths are relative to the task manifest.  The
                # batch is persistent, while each controller runs from a new
                # ulhpc-submit source snapshot whose absolute path changes.
                "rules_path": "../candidate_rules.txt",
                "checker_payload": case.checker_payload(),
            }
            if case.repetition_index is not None:
                payload["repetition_index"] = case.repetition_index
            if evaluation_tag is not None:
                payload["evaluation_tag"] = evaluation_tag
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
                "evaluation_tag": evaluation_tag,
                "instance_ids": [case.instance_id for case in batch],
                "repetition_indices": [
                    case.repetition_index for case in batch
                ],
                "contains_historical_labels": False,
            },
        )
        return tasks
