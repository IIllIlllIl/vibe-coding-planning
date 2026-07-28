"""HPC/Slurm executor support for online GEPA rollout batches.

This module deliberately keeps GEPA state local to the controller. It only
materializes rollout task manifests and provides a collection path for worker
outputs produced by a Slurm job array.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import time
from typing import Any, Callable, Sequence

from src.optimization.audit import JsonlLogger, text_sha256
from src.exceptions import FatalError, OnlineControllerYield
from src.optimization.hpc.slurm import (
    SlurmTaskStatus as SlurmTaskStatus,
    collect_slurm_resource_snapshot,
    normalize_slurm_state,
    parse_slurm_duration,
    parse_slurm_task_status as parse_slurm_task_status,
    query_slurm_array_job_id,
    query_slurm_task_status,
    submit_slurm_array,
)
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.online_models import (
    ONLINE_OUTCOME_POLICY_VERSION,
    OnlineGEPACase,
    OnlineRolloutOutput,
    scored_agent_failure,
)


_SLURM_TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}
_SLURM_PENDING_STATES = {
    "CONFIGURING",
    "PENDING",
    "REQUEUE_FED",
    "REQUEUE_HOLD",
    "REQUEUED",
    "RESIZING",
    "SUSPENDED",
}


def _stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rollout_source_sha256() -> str:
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted((root / "src").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def rollout_semantic_sha256(config: OnlineOptimizationConfig) -> str:
    """Hash every setting that can change a worker's rollout result."""
    return _stable_sha256(
        {
            "schema": 2,
            "outcome_policy_version": ONLINE_OUTCOME_POLICY_VERSION,
            "rollout_source_sha256": _rollout_source_sha256(),
            "dataset": asdict(config.dataset),
            "plan": asdict(config.plan),
            "code": asdict(config.code),
            "docker": asdict(config.docker),
            "container": asdict(config.container),
            "evaluator": asdict(config.evaluator),
            "code_phase_timeout_seconds": (
                config.execution.code_phase_timeout_seconds
            ),
            "separate_reflection_tasks": (
                config.execution.separate_reflection_tasks
            ),
            "agent_failure_max_task_attempts": config.hpc.max_task_attempts,
            "prompts": {
                "plan": config.plan_prompt,
                "plan_instance": config.plan_instance_template,
                "code": config.code_prompt,
                "code_instance": config.code_instance_template,
                "reflection_reviewer": config.reflection_reviewer_prompt,
                "reflection_reviewer_instance": (
                    config.reflection_reviewer_instance_template
                ),
                "nrpv": config.nrpv_block,
            },
        }
    )


def evaluation_fingerprint(
    config: OnlineOptimizationConfig,
    *,
    batch: Sequence[OnlineGEPACase],
    rules: str,
    capture_traces: bool,
) -> tuple[str, str]:
    semantic_hash = rollout_semantic_sha256(config)
    return (
        _stable_sha256(
            {
                "schema": 1,
                "outcome_policy_version": ONLINE_OUTCOME_POLICY_VERSION,
                "rollout_semantic_sha256": semantic_hash,
                "candidate_sha256": text_sha256(rules),
                "capture_traces": capture_traces,
                "instances": [case.instance_id for case in batch],
                "splits": [case.split for case in batch],
                "case_payloads": [case.rollout_payload() for case in batch],
            }
        ),
        semantic_hash,
    )


@dataclass(frozen=True)
class OnlineRolloutTask:
    index: int
    case: OnlineGEPACase
    rules_path: Path
    manifest_path: Path
    output_path: Path
    worker_run_dir: Path


class AgentWorkerFailure(RuntimeError):
    """A structured worker failure attributable to Plan/Code behavior."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(
            f"{data.get('terminal_phase')}:{data.get('terminal_reason')}: "
            f"{data.get('error', '')}"
        )
        self.data = data


def _is_timeout_reason(reason: str) -> bool:
    return "timeout" in reason.lower() or reason.lower().endswith(
        "deadline_exceeded"
    )


class OnlineRolloutBatchStore:
    """Create local task manifests and collect worker output JSON files."""

    def __init__(self, run_dir: Path) -> None:
        self.root = run_dir / "hpc_rollout_batches"
        self.root.mkdir(parents=True, exist_ok=True)

    def next_batch_dir(self) -> Path:
        existing = [
            int(path.name.rsplit("_", 1)[-1])
            for path in self.root.glob("batch_*")
            if path.is_dir() and path.name.rsplit("_", 1)[-1].isdigit()
        ]
        return self.root / f"batch_{max(existing, default=0) + 1:04d}"

    def find_by_fingerprint(self, fingerprint: str) -> Path | None:
        for path in sorted(self.root.glob("batch_*"), reverse=True):
            manifest_path = path / "manifest.json"
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("evaluation_fingerprint") == fingerprint:
                return path
        return None

    def create(
        self,
        *,
        batch: Sequence[OnlineGEPACase],
        rules: str,
        split: str | None,
        capture_traces: bool,
        evaluation_fingerprint: str = "",
        rollout_semantic_sha256: str = "",
    ) -> tuple[Path, list[OnlineRolloutTask]]:
        batch_dir = self.next_batch_dir()
        task_dir = batch_dir / "tasks"
        output_dir = batch_dir / "outputs"
        worker_run_root = batch_dir / "worker_runs"
        task_dir.mkdir(parents=True)
        output_dir.mkdir()
        worker_run_root.mkdir()
        rules_path = batch_dir / "candidate_rules.txt"
        rules_path.write_text(rules, encoding="utf-8")
        candidate_sha256 = text_sha256(rules)
        tasks: list[OnlineRolloutTask] = []
        for index, case in enumerate(batch):
            manifest_path = task_dir / f"task_{index:04d}.json"
            output_path = output_dir / f"task_{index:04d}.json"
            worker_run_dir = worker_run_root / f"task_{index:04d}"
            payload = {
                "index": index,
                "mode": "online_planning",
                "instance_id": case.instance_id,
                "split": case.split,
                "issue_description": case.issue_description,
                "repository": {
                    "repo": case.repository.repo,
                    "base_commit": case.repository.base_commit,
                    "instance_id": case.repository.instance_id,
                },
                "rules_path": str(rules_path),
                "candidate_sha256": candidate_sha256,
                "capture_traces": capture_traces,
                "evaluation_fingerprint": evaluation_fingerprint,
                "rollout_semantic_sha256": rollout_semantic_sha256,
            }
            manifest_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            tasks.append(
                OnlineRolloutTask(
                    index=index,
                    case=case,
                    rules_path=rules_path,
                    manifest_path=manifest_path,
                    output_path=output_path,
                    worker_run_dir=worker_run_dir,
                )
            )
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "mode": "online_planning_hpc_batch",
                    "candidate_sha256": candidate_sha256,
                    "split": split,
                    "capture_traces": capture_traces,
                    "task_count": len(tasks),
                    "instances": [case.instance_id for case in batch],
                    "evaluation_fingerprint": evaluation_fingerprint,
                    "rollout_semantic_sha256": rollout_semantic_sha256,
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return batch_dir, tasks

    @staticmethod
    def tasks_for_existing(
        batch_dir: Path,
        batch: Sequence[OnlineGEPACase],
    ) -> list[OnlineRolloutTask]:
        rules_path = batch_dir / "candidate_rules.txt"
        return [
            OnlineRolloutTask(
                index=index,
                case=case,
                rules_path=rules_path,
                manifest_path=batch_dir / "tasks" / f"task_{index:04d}.json",
                output_path=batch_dir / "outputs" / f"task_{index:04d}.json",
                worker_run_dir=batch_dir / "worker_runs" / f"task_{index:04d}",
            )
            for index, case in enumerate(batch)
        ]

    @staticmethod
    def load_output(
        path: Path,
        *,
        expected_instance_id: str | None = None,
        expected_candidate_sha256: str | None = None,
    ) -> OnlineRolloutOutput:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "agent_failed":
            if not data.get("terminal_phase") or not data.get("terminal_reason"):
                raise RuntimeError("agent-failed output lacks terminal classification")
            raise AgentWorkerFailure(data)
        if data.get("status") != "completed":
            raise RuntimeError(
                f"online rollout worker failed for {data.get('instance_id')}: "
                f"{data.get('error_type')}: {data.get('error')}"
            )
        if (
            expected_instance_id is not None
            and data.get("instance_id") != expected_instance_id
        ):
            raise RuntimeError("online rollout output instance ID mismatch")
        if (
            expected_candidate_sha256 is not None
            and data.get("candidate_sha256") != expected_candidate_sha256
        ):
            raise RuntimeError("online rollout output candidate hash mismatch")
        return OnlineRolloutOutput(
            resolved=bool(data["resolved"]),
            plan=str(data["plan"]),
            patch=str(data["patch"]),
            plan_trajectory=tuple(data.get("plan_trajectory", [])),
            code_trajectory=tuple(data.get("code_trajectory", [])),
            evaluator_result=dict(data.get("evaluator_result", {})),
            terminal_phase=data.get("terminal_phase"),
            terminal_reason=data.get("terminal_reason"),
            reflection_review=data.get("reflection_review"),
            reflection_reviewer_trajectory=tuple(
                data.get("reflection_reviewer_trajectory", [])
            ),
        )


def build_slurm_array_script(
    *,
    config_path: str,
    batch_dir: str,
    task_count: int,
    job_name: str,
    partition: str,
    cpus_per_task: int,
    mem: str,
    time_limit: str,
    max_running_array_tasks: int,
    remote_env_file: str,
    python_module: str,
    container_module: str,
    python_bin: str,
    task_indices: Sequence[int] | None = None,
    attempt: int = 1,
) -> str:
    if task_count < 1:
        raise ValueError("task_count must be positive")
    if task_indices is None:
        index_spec = f"0-{task_count - 1}"
    else:
        indices = list(task_indices)
        if not indices:
            raise ValueError("task_indices must not be empty")
        if any(index < 0 or index >= task_count for index in indices):
            raise ValueError("task_indices must be within task_count")
        index_spec = ",".join(str(index) for index in indices)
    array_spec = f"{index_spec}%{max_running_array_tasks}"
    quoted_config = shlex.quote(config_path)
    quoted_batch_dir = shlex.quote(batch_dir)
    quoted_python = shlex.quote(python_bin)
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --cpus-per-task={cpus_per_task}",
        f"#SBATCH --mem={mem}",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --array={array_spec}",
        "#SBATCH --output=%x-%A_%a.out",
        "#SBATCH --error=%x-%A_%a.err",
        "set -euo pipefail",
        "set +x",
        f"module load {shlex.quote(python_module)}",
        f"module load {shlex.quote(container_module)}",
        f"ENV_FILE={shlex.quote(remote_env_file)}",
        'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
        'source "${ENV_FILE}"',
        'test -n "${DEEPSEEK_API_KEY:-}" || { echo "missing DEEPSEEK_API_KEY" >&2; exit 2; }',
        f"BATCH_DIR={quoted_batch_dir}",
        'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"',
        'TASK_MANIFEST="${BATCH_DIR}/tasks/task_${TASK_ID}.json"',
        'OUTPUT_JSON="${BATCH_DIR}/outputs/task_${TASK_ID}.json"',
        f"ATTEMPT={attempt}",
        'WORKER_RUN_DIR="${BATCH_DIR}/worker_runs/task_${TASK_ID}/attempt_${ATTEMPT}"',
        f"{quoted_python} -m src.optimization.online_rollout_worker "
        f"--config {quoted_config} "
        "--task-manifest \"${TASK_MANIFEST}\" "
        "--output \"${OUTPUT_JSON}\" "
        "--worker-run-dir \"${WORKER_RUN_DIR}\"",
    ]
    return "\n".join(lines) + "\n"


def build_reviewer_array_script(
    *,
    config_path: str,
    batch_dir: str,
    task_count: int,
    job_name: str,
    hpc: Any,
    task_indices: Sequence[int],
    attempt: int,
) -> str:
    index_spec = ",".join(str(index) for index in task_indices)
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}",
        f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}",
        f"#SBATCH --time={hpc.time}",
        f"#SBATCH --array={index_spec}%{hpc.max_running_array_tasks}",
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
        f"BATCH_DIR={shlex.quote(batch_dir)}",
        'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"',
        f"ATTEMPT={attempt}",
        'ATTEMPT_ID="$(printf "%02d" "${ATTEMPT}")"',
        'TASK_MANIFEST="${BATCH_DIR}/reviewer/tasks/task_${TASK_ID}.json"',
        'OUTPUT_JSON="${BATCH_DIR}/reviewer/outputs/task_${TASK_ID}.json"',
        'ATTEMPT_DIR="${BATCH_DIR}/reviewer/attempts/task_${TASK_ID}/attempt_${ATTEMPT_ID}"',
        f"{shlex.quote(hpc.python_bin)} -m src.optimization.online_reviewer_worker "
        f"--config {shlex.quote(config_path)} "
        '--task-manifest "${TASK_MANIFEST}" '
        '--output "${OUTPUT_JSON}" '
        '--attempt-dir "${ATTEMPT_DIR}"',
    ]
    return "\n".join(lines) + "\n"


class HPCSlurmOnlineRolloutExecutor:
    """Prepare, optionally submit, and collect a Slurm rollout batch."""

    def __init__(
        self,
        config: OnlineOptimizationConfig,
        *,
        submitter: Callable[[Path], str | None] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.store = OnlineRolloutBatchStore(config.run_dir)
        self.submitter = submitter
        self.sleep = sleeper
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    def _yield_for_workers(
        self,
        batch_dir: Path,
        job_id: str | None,
        *,
        reason: str,
    ) -> None:
        if not self.config.execution.controller_yield_after_submit:
            return
        self.audit.write(
            "online_controller_yield_requested",
            batch_dir=str(batch_dir),
            job_id=job_id,
            reason=reason,
        )
        raise OnlineControllerYield(
            batch_dir=str(batch_dir),
            job_id=job_id,
            reason=reason,
        )

    def evaluate(
        self,
        batch: list[OnlineGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[OnlineRolloutOutput]:
        split = next((case.split for case in batch), None)
        fingerprint, semantic_hash = evaluation_fingerprint(
            self.config,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
        )
        batch_dir = self.store.find_by_fingerprint(fingerprint)
        if batch_dir is None:
            batch_dir, tasks = self.store.create(
                batch=batch,
                rules=rules,
                split=split,
                capture_traces=capture_traces,
                evaluation_fingerprint=fingerprint,
                rollout_semantic_sha256=semantic_hash,
            )
            self._write_array_script(
                batch_dir=batch_dir,
                tasks=tasks,
                total_task_count=len(tasks),
                attempt=1,
            )
            self._write_batch_state(
                batch_dir,
                phase="PREPARED",
                evaluation_fingerprint=fingerprint,
                active_attempt=1,
                active_job_id=None,
                retry_job_ids=[],
            )
            self.audit.write(
                "online_hpc_rollout_batch_prepared",
                batch_dir=str(batch_dir),
                evaluation_fingerprint=fingerprint,
                task_count=len(tasks),
                submit=self.config.hpc.submit,
                cpus_per_task=self.config.hpc.cpus_per_task,
                mem=self.config.hpc.mem,
                time=self.config.hpc.time,
                max_running_array_tasks=self.config.hpc.max_running_array_tasks,
            )
        else:
            tasks = self.store.tasks_for_existing(batch_dir, batch)
            self._validate_existing_batch(
                batch_dir,
                tasks=tasks,
                rules=rules,
                fingerprint=fingerprint,
            )
            self.audit.write(
                "online_hpc_rollout_batch_resumed",
                batch_dir=str(batch_dir),
                evaluation_fingerprint=fingerprint,
                completed_outputs=sum(task.output_path.is_file() for task in tasks),
            )
        if self.config.hpc.submit:
            state = self._read_batch_state(batch_dir, fingerprint=fingerprint)
            job_id = state.get("active_job_id")
            attempt = int(state.get("active_attempt", 1))
            retry_job_ids = [str(item) for item in state.get("retry_job_ids", [])]
            before: dict[str, Any] = {}
            if state.get("phase") == "COMPLETE":
                if not (batch_dir / "batch_done.json").is_file():
                    raise RuntimeError(
                        "online HPC COMPLETE batch lacks batch_done.json"
                    )
                outputs = self._load_completed_outputs(tasks)
                self.audit.write(
                    "online_hpc_rollout_batch_reused_complete",
                    batch_dir=str(batch_dir),
                    job_id=job_id,
                    completed_outputs=len(outputs),
                )
                return outputs
            if all(task.output_path.is_file() for task in tasks):
                self._write_batch_state(
                    batch_dir,
                    phase="OUTPUTS_READY",
                    evaluation_fingerprint=fingerprint,
                    active_attempt=attempt,
                    active_job_id=job_id,
                    retry_job_ids=retry_job_ids,
                )
                self.audit.write(
                    "online_hpc_rollout_batch_outputs_ready",
                    batch_dir=str(batch_dir),
                    job_id=job_id,
                    output_count=len(tasks),
                )
            if not all(task.output_path.is_file() for task in tasks):
                should_yield = False
                if not job_id:
                    if state.get("phase") == "SUBMITTING":
                        job_name = (
                            f"{self.config.hpc.job_name_prefix}-{batch_dir.name}"
                            f"-a{attempt}"
                        )
                        job_id = query_slurm_array_job_id(job_name)
                        if not job_id:
                            raise RuntimeError(
                                "online HPC batch submission outcome is ambiguous; "
                                "the deterministic Slurm job name is not visible yet"
                            )
                        self._write_batch_state(
                            batch_dir,
                            phase="SUBMITTED",
                            evaluation_fingerprint=fingerprint,
                            active_attempt=attempt,
                            active_job_id=job_id,
                            retry_job_ids=retry_job_ids,
                        )
                        self.audit.write(
                            "online_hpc_rollout_submission_reconciled",
                            batch_dir=str(batch_dir),
                            job_id=job_id,
                            attempt=attempt,
                            job_name=job_name,
                        )
                        should_yield = True
                        state = self._read_batch_state(
                            batch_dir,
                            fingerprint=fingerprint,
                        )
                    if job_id:
                        before = {}
                    else:
                        before = collect_slurm_resource_snapshot()
                        script_path = batch_dir / f"rollout_array_attempt_{attempt:02d}.sbatch"
                        self._write_batch_state(
                            batch_dir,
                            phase="SUBMITTING",
                            evaluation_fingerprint=fingerprint,
                            active_attempt=attempt,
                            active_job_id=None,
                            retry_job_ids=retry_job_ids,
                        )
                        job_id = (self.submitter or submit_slurm_array)(script_path)
                        self._write_batch_state(
                            batch_dir,
                            phase="SUBMITTED",
                            evaluation_fingerprint=fingerprint,
                            active_attempt=attempt,
                            active_job_id=job_id,
                            retry_job_ids=retry_job_ids,
                        )
                        self.audit.write(
                            "online_hpc_rollout_batch_submitted",
                            batch_dir=str(batch_dir),
                            job_id=job_id,
                            attempt=attempt,
                            fairshare_before=before.get("ulhpcshare_stdout", ""),
                        )
                        should_yield = True
                if should_yield:
                    self._yield_for_workers(
                        batch_dir,
                        str(job_id) if job_id else None,
                        reason="waiting_for_rollout_array",
                    )
                missing = self._wait_for_outputs(
                    tasks,
                    job_id=str(job_id) if job_id else None,
                    attempt=attempt,
                )
            else:
                missing = []
            outputs, retry_job_ids = self._load_outputs_with_retries(
                batch_dir,
                tasks,
                initial_missing=missing,
                starting_attempt=attempt,
                retry_job_ids=retry_job_ids,
                evaluation_fingerprint=fingerprint,
            )
            if capture_traces and self.config.execution.separate_reflection_tasks:
                outputs = self._ensure_reviews(
                    batch_dir,
                    tasks,
                    outputs,
                    evaluation_fingerprint=fingerprint,
                )
            after = collect_slurm_resource_snapshot(str(job_id) if job_id else None)
            usage = {
                "mode": "online_planning_hpc_batch_resource_usage",
                "batch_dir": str(batch_dir),
                "job_id": job_id,
                "retry_job_ids": retry_job_ids,
                "task_count": len(tasks),
                "cpus_per_task": self.config.hpc.cpus_per_task,
                "mem": self.config.hpc.mem,
                "time": self.config.hpc.time,
                "max_running_array_tasks": self.config.hpc.max_running_array_tasks,
                "before": before,
                "after": after,
            }
            _write_json(batch_dir / "resource_usage.json", usage)
            _write_json(
                batch_dir / "batch_done.json",
                {
                    "mode": "online_planning_hpc_batch_done",
                    "job_id": job_id,
                    "retry_job_ids": retry_job_ids,
                    "task_count": len(tasks),
                    "completed_outputs": len(outputs),
                    "attempts": self.config.hpc.max_task_attempts,
                    "evaluation_fingerprint": fingerprint,
                },
            )
            latest_state = self._read_batch_state(
                batch_dir,
                fingerprint=fingerprint,
            )
            self._write_batch_state(
                batch_dir,
                phase="COMPLETE",
                evaluation_fingerprint=fingerprint,
                active_attempt=int(latest_state.get("active_attempt", attempt)),
                active_job_id=latest_state.get("active_job_id", job_id),
                retry_job_ids=retry_job_ids,
            )
            self.audit.write(
                "online_hpc_rollout_batch_completed",
                batch_dir=str(batch_dir),
                job_id=job_id,
                completed_outputs=len(outputs),
                fairshare_after=after.get("ulhpcshare_stdout", ""),
            )
            return outputs
        raise RuntimeError(
            "HPC rollout batch prepared but not submitted. Review "
            f"{batch_dir} and set hpc.submit=true after resource pilot setup."
        )

    def _ensure_reviews(
        self,
        batch_dir: Path,
        tasks: list[OnlineRolloutTask],
        outputs: list[OnlineRolloutOutput],
        *,
        evaluation_fingerprint: str,
    ) -> list[OnlineRolloutOutput]:
        root = batch_dir / "reviewer"
        task_root = root / "tasks"
        output_root = root / "outputs"
        state_path = root / "task_state.json"
        task_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(exist_ok=True)
        for task in tasks:
            path = task_root / f"task_{task.index:04d}.json"
            if not path.exists():
                _write_json(
                    path,
                    {
                        "schema_version": 1,
                        "evaluation_fingerprint": evaluation_fingerprint,
                        "instance_id": task.case.instance_id,
                        "rollout_manifest_path": str(task.manifest_path),
                        "rollout_output_path": str(task.output_path),
                    },
                )
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.is_file()
            else {
                "schema_version": 1,
                "evaluation_fingerprint": evaluation_fingerprint,
                "attempt": 1,
                "job_id": None,
            }
        )
        if state.get("evaluation_fingerprint") != evaluation_fingerprint:
            raise FatalError("reviewer task fingerprint mismatch")

        while True:
            attempt = int(state["attempt"])
            retry: list[int] = []
            missing_outputs: set[int] = set()
            completed: dict[int, dict[str, Any]] = {}
            for task in tasks:
                path = output_root / f"task_{task.index:04d}.json"
                if not path.is_file():
                    retry.append(task.index)
                    missing_outputs.add(task.index)
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("status") == "completed":
                    if (
                        payload.get("instance_id") is not None
                        and payload.get("instance_id") != task.case.instance_id
                    ):
                        raise FatalError("reviewer output instance ID mismatch")
                    if (
                        payload.get("candidate_sha256") is not None
                        and payload.get("candidate_sha256")
                        != text_sha256(task.rules_path.read_text(encoding="utf-8"))
                    ):
                        raise FatalError("reviewer output candidate hash mismatch")
                    completed[task.index] = payload
                elif payload.get("status") == "blocking_failed":
                    raise FatalError(
                        "reviewer infrastructure failure: "
                        f"{task.case.instance_id}: {payload.get('error')}"
                    )
                else:
                    retry.append(task.index)
            if not retry:
                merged = []
                for task, output in zip(tasks, outputs, strict=True):
                    payload = completed[task.index]
                    reviewed = OnlineRolloutOutput(
                            resolved=output.resolved,
                            plan=output.plan,
                            patch=output.patch,
                            plan_trajectory=output.plan_trajectory,
                            code_trajectory=output.code_trajectory,
                            evaluator_result=output.evaluator_result,
                            terminal_phase=output.terminal_phase,
                            terminal_reason=output.terminal_reason,
                            reflection_review=dict(payload["review"]),
                            reflection_reviewer_trajectory=tuple(
                                payload.get("trajectory", [])
                            ),
                        )
                    merged.append(reviewed)
                    persisted = json.loads(
                        task.output_path.read_text(encoding="utf-8")
                    )
                    persisted.update(reviewed.to_worker_payload())
                    _write_json(task.output_path, persisted)
                _write_json(
                    state_path,
                    {**state, "status": "COMPLETE", "job_id": state.get("job_id")},
                )
                self.audit.write(
                    "online_hpc_reviewer_completed",
                    batch_dir=str(batch_dir),
                    attempt=attempt,
                    task_count=len(tasks),
                )
                return merged

            job_id = state.get("job_id")
            if job_id:
                active = False
                missing_status = False
                for index in retry:
                    status = query_slurm_task_status(str(job_id), index)
                    if status is None:
                        missing_status = True
                    elif normalize_slurm_state(status.state) in (
                        _SLURM_PENDING_STATES | {"RUNNING", "COMPLETING"}
                    ):
                        active = True
                    elif normalize_slurm_state(status.state) in {
                        "OUT_OF_MEMORY",
                        "NODE_FAIL",
                        "BOOT_FAIL",
                    }:
                        raise FatalError(
                            f"reviewer infrastructure failure: {status.state}"
                        )
                    elif index in missing_outputs:
                        _write_json(
                            root
                            / "attempts"
                            / f"task_{index:04d}"
                            / f"attempt_{attempt:02d}"
                            / "slurm_status.json",
                            {
                                "state": normalize_slurm_state(status.state),
                                "elapsed_seconds": status.elapsed_seconds,
                                "raw": status.raw,
                            },
                        )
                        terminal_missing_since = state.get("terminal_missing_since")
                        if terminal_missing_since is None:
                            state = {**state, "terminal_missing_since": time.time()}
                            _write_json(state_path, state)
                            active = True
                        elif (
                            time.time() - float(terminal_missing_since)
                            < self.config.hpc.task_output_grace_seconds
                        ):
                            active = True
                if missing_status:
                    missing_since = state.get("missing_since")
                    if missing_since is None:
                        state = {**state, "missing_since": time.time()}
                        _write_json(state_path, state)
                        active = True
                    elif (
                        time.time() - float(missing_since)
                        < self.config.hpc.missing_task_grace_seconds
                    ):
                        active = True
                if active:
                    self._yield_for_workers(
                        root,
                        str(job_id),
                        reason="waiting_for_reviewer_array",
                    )
            if attempt >= self.config.hpc.max_task_attempts:
                for index in retry:
                    task = tasks[index]
                    completed[index] = {
                        "instance_id": task.case.instance_id,
                        "candidate_sha256": text_sha256(
                            task.rules_path.read_text(encoding="utf-8")
                        ),
                        "review": {
                            "review_status": "unavailable",
                            "instance_id": task.case.instance_id,
                            "reason": "reviewer attempts exhausted",
                        },
                        "trajectory": [],
                    }
                    _write_json(
                        output_root / f"task_{index:04d}.json",
                        {"status": "completed", **completed[index]},
                    )
                state = {**state, "status": "EXHAUSTED", "job_id": None}
                _write_json(state_path, state)
                self.audit.write(
                    "online_hpc_reviewer_exhausted",
                    batch_dir=str(batch_dir),
                    attempt=attempt,
                    task_indices=retry,
                )
                continue

            next_attempt = attempt if not job_id else attempt + 1
            script = root / f"reviewer_attempt_{next_attempt:02d}.sbatch"
            script.write_text(
                build_reviewer_array_script(
                    config_path=self.config.hpc.worker_config_path,
                    batch_dir=str(batch_dir),
                    task_count=len(tasks),
                    job_name=(
                        f"{self.config.hpc.job_name_prefix}-review-{batch_dir.name}"
                        f"-a{next_attempt}"
                    ),
                    hpc=self.config.hpc,
                    task_indices=retry,
                    attempt=next_attempt,
                ),
                encoding="utf-8",
            )
            submitted = (self.submitter or submit_slurm_array)(script)
            state = {
                "schema_version": 1,
                "evaluation_fingerprint": evaluation_fingerprint,
                "status": "SUBMITTED",
                "attempt": next_attempt,
                "job_id": submitted,
                "missing_since": None,
                "terminal_missing_since": None,
            }
            _write_json(state_path, state)
            self.audit.write(
                "online_hpc_reviewer_submitted",
                batch_dir=str(batch_dir),
                attempt=next_attempt,
                job_id=submitted,
                task_indices=retry,
            )
            self._yield_for_workers(
                root,
                submitted,
                reason="waiting_for_reviewer_array",
            )

    def _load_completed_outputs(
        self,
        tasks: list[OnlineRolloutTask],
    ) -> list[OnlineRolloutOutput]:
        candidate_sha256 = text_sha256(
            tasks[0].rules_path.read_text(encoding="utf-8")
        )
        return [
            self.store.load_output(
                task.output_path,
                expected_instance_id=task.case.instance_id,
                expected_candidate_sha256=candidate_sha256,
            )
            for task in tasks
        ]

    @staticmethod
    def _write_batch_state(
        batch_dir: Path,
        *,
        phase: str,
        evaluation_fingerprint: str,
        active_attempt: int,
        active_job_id: str | None,
        retry_job_ids: list[str],
    ) -> None:
        _write_json(
            batch_dir / "batch_state.json",
            {
                "schema_version": 1,
                "phase": phase,
                "evaluation_fingerprint": evaluation_fingerprint,
                "active_attempt": active_attempt,
                "active_job_id": active_job_id,
                "retry_job_ids": retry_job_ids,
            },
        )

    @staticmethod
    def _read_batch_state(
        batch_dir: Path,
        *,
        fingerprint: str,
    ) -> dict[str, Any]:
        path = batch_dir / "batch_state.json"
        if not path.is_file():
            raise RuntimeError(f"resumable online HPC batch lacks {path.name}")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1:
            raise RuntimeError("unsupported online HPC batch state schema")
        if state.get("evaluation_fingerprint") != fingerprint:
            raise RuntimeError("online HPC batch state fingerprint mismatch")
        return state

    @staticmethod
    def _validate_existing_batch(
        batch_dir: Path,
        *,
        tasks: list[OnlineRolloutTask],
        rules: str,
        fingerprint: str,
    ) -> None:
        manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("evaluation_fingerprint") != fingerprint:
            raise RuntimeError("online HPC batch manifest fingerprint mismatch")
        if manifest.get("candidate_sha256") != text_sha256(rules):
            raise RuntimeError("online HPC batch candidate hash mismatch")
        if manifest.get("instances") != [task.case.instance_id for task in tasks]:
            raise RuntimeError("online HPC batch instance order mismatch")
        if manifest.get("task_count") != len(tasks):
            raise RuntimeError("online HPC batch task count mismatch")
        for task in tasks:
            task_manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
            if task_manifest.get("instance_id") != task.case.instance_id:
                raise RuntimeError("online HPC task manifest instance mismatch")
            if task_manifest.get("candidate_sha256") != text_sha256(rules):
                raise RuntimeError("online HPC task manifest candidate mismatch")

    def _write_array_script(
        self,
        *,
        batch_dir: Path,
        tasks: list[OnlineRolloutTask],
        total_task_count: int,
        attempt: int,
    ) -> Path:
        script = build_slurm_array_script(
            config_path=self.config.hpc.worker_config_path,
            batch_dir=str(batch_dir),
            task_count=total_task_count,
            job_name=f"{self.config.hpc.job_name_prefix}-{batch_dir.name}-a{attempt}",
            partition=self.config.hpc.partition,
            cpus_per_task=self.config.hpc.cpus_per_task,
            mem=self.config.hpc.mem,
            time_limit=self.config.hpc.time,
            max_running_array_tasks=self.config.hpc.max_running_array_tasks,
            remote_env_file=self.config.hpc.remote_env_file,
            python_module=self.config.hpc.python_module,
            container_module=self.config.hpc.container_module,
            python_bin=self.config.hpc.python_bin,
            task_indices=None if attempt == 1 else [task.index for task in tasks],
            attempt=attempt,
        )
        script_path = batch_dir / f"rollout_array_attempt_{attempt:02d}.sbatch"
        script_path.write_text(script, encoding="utf-8")
        return script_path

    def _load_outputs_with_retries(
        self,
        batch_dir: Path,
        tasks: list[OnlineRolloutTask],
        *,
        initial_missing: list[OnlineRolloutTask] | None = None,
        starting_attempt: int = 1,
        retry_job_ids: list[str] | None = None,
        evaluation_fingerprint: str = "",
    ) -> tuple[list[OnlineRolloutOutput], list[str]]:
        retry_job_ids = list(retry_job_ids or [])
        outputs_by_index: dict[int, OnlineRolloutOutput] = {}
        pending = list(tasks)
        missing_by_attempt: dict[int, list[OnlineRolloutTask]] = {
            starting_attempt: list(initial_missing or [])
        }
        for attempt in range(
            starting_attempt,
            self.config.hpc.max_task_attempts + 1,
        ):
            failed_by_index: dict[int, OnlineRolloutTask] = {
                task.index: task for task in missing_by_attempt.pop(attempt, [])
            }
            agent_failures: dict[int, dict[str, Any]] = {}
            for task in pending:
                if task.index in failed_by_index:
                    self.audit.write(
                        "online_hpc_rollout_task_missing_output",
                        batch_dir=str(batch_dir),
                        instance_id=task.case.instance_id,
                        task_index=task.index,
                        attempt=attempt,
                        max_task_attempts=self.config.hpc.max_task_attempts,
                    )
                    continue
                try:
                    outputs_by_index[task.index] = self.store.load_output(
                        task.output_path,
                        expected_instance_id=task.case.instance_id,
                        expected_candidate_sha256=text_sha256(
                            task.rules_path.read_text(encoding="utf-8")
                        ),
                    )
                except AgentWorkerFailure as exc:
                    failed_by_index[task.index] = task
                    agent_failures[task.index] = exc.data
                    self.audit.write(
                        "online_hpc_rollout_agent_failure",
                        batch_dir=str(batch_dir),
                        instance_id=task.case.instance_id,
                        task_index=task.index,
                        attempt=attempt,
                        max_task_attempts=self.config.hpc.max_task_attempts,
                        terminal_phase=exc.data["terminal_phase"],
                        terminal_reason=exc.data["terminal_reason"],
                    )
                    self._archive_failed_output(batch_dir, task, attempt)
                except Exception as exc:
                    failed_by_index[task.index] = task
                    self.audit.write(
                        "online_hpc_rollout_task_failed",
                        batch_dir=str(batch_dir),
                        instance_id=task.case.instance_id,
                        task_index=task.index,
                        attempt=attempt,
                        max_task_attempts=self.config.hpc.max_task_attempts,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._archive_failed_output(batch_dir, task, attempt)
            failed = [failed_by_index[index] for index in sorted(failed_by_index)]
            if not failed:
                return (
                    [outputs_by_index[task.index] for task in tasks],
                    retry_job_ids,
                )
            if attempt >= self.config.hpc.max_task_attempts:
                infrastructure_failed = [
                    task for task in failed if task.index not in agent_failures
                ]
                for task in failed:
                    failure = agent_failures.get(task.index)
                    if failure is None:
                        continue
                    self._finalize_agent_failure(task, failure, attempt)
                    outputs_by_index[task.index] = self.store.load_output(
                        task.output_path,
                        expected_instance_id=task.case.instance_id,
                        expected_candidate_sha256=text_sha256(
                            task.rules_path.read_text(encoding="utf-8")
                        ),
                    )
                    self.audit.write(
                        "online_hpc_rollout_agent_failure_scored",
                        batch_dir=str(batch_dir),
                        instance_id=task.case.instance_id,
                        task_index=task.index,
                        attempt=attempt,
                        score=0.0,
                        terminal_phase=failure["terminal_phase"],
                        terminal_reason=failure["terminal_reason"],
                    )
                if not infrastructure_failed:
                    return (
                        [outputs_by_index[task.index] for task in tasks],
                        retry_job_ids,
                    )
                failed_ids = ", ".join(
                    task.case.instance_id for task in infrastructure_failed
                )
                raise RuntimeError(
                    "online HPC infrastructure-invalid rollout tasks failed after "
                    f"{attempt} attempts: {failed_ids}"
                )
            retry_attempt = attempt + 1
            retry_script = self._write_array_script(
                batch_dir=batch_dir,
                tasks=failed,
                total_task_count=len(tasks),
                attempt=retry_attempt,
            )
            before = collect_slurm_resource_snapshot()
            if evaluation_fingerprint:
                self._write_batch_state(
                    batch_dir,
                    phase="SUBMITTING",
                    evaluation_fingerprint=evaluation_fingerprint,
                    active_attempt=retry_attempt,
                    active_job_id=None,
                    retry_job_ids=retry_job_ids,
                )
            job_id = (self.submitter or submit_slurm_array)(retry_script)
            if job_id is not None:
                retry_job_ids.append(job_id)
            if evaluation_fingerprint:
                self._write_batch_state(
                    batch_dir,
                    phase="SUBMITTED",
                    evaluation_fingerprint=evaluation_fingerprint,
                    active_attempt=retry_attempt,
                    active_job_id=job_id,
                    retry_job_ids=retry_job_ids,
                )
            self.audit.write(
                "online_hpc_rollout_retry_submitted",
                batch_dir=str(batch_dir),
                attempt=retry_attempt,
                job_id=job_id,
                task_indices=[task.index for task in failed],
                instance_ids=[task.case.instance_id for task in failed],
                fairshare_before=before.get("ulhpcshare_stdout", ""),
            )
            self._yield_for_workers(
                batch_dir,
                job_id,
                reason="waiting_for_retry_array",
            )
            missing_by_attempt[retry_attempt] = self._wait_for_outputs(
                failed,
                job_id=job_id,
                attempt=retry_attempt,
            )
            pending = failed
        raise RuntimeError("unreachable online HPC retry state")

    @staticmethod
    def _archive_failed_output(
        batch_dir: Path,
        task: OnlineRolloutTask,
        attempt: int,
    ) -> None:
        if not task.output_path.exists():
            return
        failed_dir = batch_dir / "failed_outputs" / f"attempt_{attempt:02d}"
        failed_dir.mkdir(parents=True, exist_ok=True)
        task.output_path.replace(failed_dir / task.output_path.name)

    @staticmethod
    def _finalize_agent_failure(
        task: OnlineRolloutTask,
        failure: dict[str, Any],
        attempt: int,
    ) -> None:
        checkpoint_dir = task.worker_run_dir / "checkpoints"

        def checkpoint_payload(phase: str) -> dict[str, Any]:
            path = checkpoint_dir / f"{phase}.json"
            if not path.is_file():
                return {}
            value = json.loads(path.read_text(encoding="utf-8"))
            payload = value.get("payload")
            return payload if isinstance(payload, dict) else {}

        plan_payload = checkpoint_payload("plan")
        code_payload = checkpoint_payload("code")
        failure_evidence = dict(failure.get("phase_evidence", {}))
        evidence = {
            "plan": plan_payload.get("plan", failure_evidence.get("plan", "")),
            "plan_trajectory": plan_payload.get(
                "plan_trajectory", failure_evidence.get("plan_trajectory", [])
            ),
            "patch": code_payload.get("patch", failure_evidence.get("patch", "")),
            "code_trajectory": code_payload.get(
                "code_trajectory", failure_evidence.get("code_trajectory", [])
            ),
        }
        output = scored_agent_failure(
            phase=str(failure["terminal_phase"]),
            reason=str(failure["terminal_reason"]),
            evidence=evidence,
        )
        _write_json(
            task.output_path,
            {
                "status": "completed",
                "outcome_policy_version": ONLINE_OUTCOME_POLICY_VERSION,
                "started_at": failure.get("started_at"),
                "finished_at": failure.get("finished_at"),
                "mode": "online_planning",
                "instance_id": task.case.instance_id,
                "split": task.case.split,
                "candidate_sha256": failure.get("candidate_sha256"),
                "attempts": attempt,
                **output.to_worker_payload(),
            },
        )

    @staticmethod
    def _finalize_slurm_timeout(task: OnlineRolloutTask, attempt: int) -> str:
        """Turn a proven worker wall-time expiry into a scored agent outcome."""
        checkpoint_dir = task.worker_run_dir / "checkpoints"

        def checkpoint_payload(phase: str) -> dict[str, Any]:
            path = checkpoint_dir / f"{phase}.json"
            if not path.is_file():
                return {}
            value = json.loads(path.read_text(encoding="utf-8"))
            payload = value.get("payload")
            return payload if isinstance(payload, dict) else {}

        plan_payload = checkpoint_payload("plan")
        code_payload = checkpoint_payload("code")
        evaluator_payload = checkpoint_payload("evaluator")
        task_manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
        if evaluator_payload and bool(task_manifest.get("capture_traces")):
            evaluator_result = dict(evaluator_payload["evaluator_result"])
            resolved = bool(evaluator_result.get("resolved", False))
            output = OnlineRolloutOutput(
                resolved=resolved,
                plan=str(plan_payload.get("plan", "")),
                patch=str(code_payload.get("patch", "")),
                plan_trajectory=tuple(plan_payload.get("plan_trajectory", [])),
                code_trajectory=tuple(code_payload.get("code_trajectory", [])),
                evaluator_result=evaluator_result,
                reflection_review={
                    "review_status": "reflection_reviewer_timeout"
                },
            )
            _write_json(
                task.output_path,
                {
                    "status": "completed",
                    "outcome_policy_version": ONLINE_OUTCOME_POLICY_VERSION,
                    "mode": "online_planning",
                    "instance_id": task.case.instance_id,
                    "split": task.case.split,
                    "candidate_sha256": text_sha256(
                        task.rules_path.read_text(encoding="utf-8")
                    ),
                    "attempts": attempt,
                    **output.to_worker_payload(),
                },
            )
            return "reviewer_timeout_preserved_evaluator"
        terminal_phase = "code" if plan_payload else "plan"
        if code_payload:
            terminal_phase = "evaluator"
        candidate_sha256 = text_sha256(task.rules_path.read_text(encoding="utf-8"))
        output = scored_agent_failure(
            phase=terminal_phase,
            reason="slurm_timeout",
            evaluator_reason="worker_slurm_timeout",
            evidence={
                "plan": plan_payload.get("plan", ""),
                "plan_trajectory": plan_payload.get("plan_trajectory", []),
                "patch": code_payload.get("patch", ""),
                "code_trajectory": code_payload.get("code_trajectory", []),
            },
        )
        _write_json(
            task.output_path,
            {
                "status": "completed",
                "outcome_policy_version": ONLINE_OUTCOME_POLICY_VERSION,
                "mode": "online_planning",
                "instance_id": task.case.instance_id,
                "split": task.case.split,
                "candidate_sha256": candidate_sha256,
                "attempts": attempt,
                **output.to_worker_payload(),
            },
        )
        return "rollout_timeout_scored_unresolved"

    def _cleanup_timed_out_workspace(
        self,
        task: OnlineRolloutTask,
        attempt: int,
    ) -> None:
        workspaces = [
            task.worker_run_dir / f"attempt_{attempt}" / "phase_workspaces",
            task.worker_run_dir / "reflection_reviewer" / "workspace",
        ]
        for workspace in workspaces:
            if not workspace.exists():
                continue
            try:
                shutil.rmtree(workspace)
            except OSError as exc:
                self.audit.write(
                    "online_hpc_rollout_timeout_cleanup_failed",
                    task_index=task.index,
                    instance_id=task.case.instance_id,
                    attempt=attempt,
                    path=str(workspace),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise FatalError(
                    "failed to clean terminal timeout workspace "
                    f"{workspace}: {exc}"
                ) from exc
            self.audit.write(
                "online_hpc_rollout_timeout_workspace_removed",
                task_index=task.index,
                instance_id=task.case.instance_id,
                attempt=attempt,
                path=str(workspace),
            )

    def _wait_for_outputs(
        self,
        tasks: list[OnlineRolloutTask],
        *,
        job_id: str | None,
        attempt: int,
    ) -> list[OnlineRolloutTask]:
        remaining = {task.index: task for task in tasks}
        first_missing_seen_at: dict[int, float] = {}
        retriable_outputs: dict[int, OnlineRolloutTask] = {}
        walltime_seconds = parse_slurm_duration(self.config.hpc.time)
        while remaining:
            now = time.monotonic()
            retriable: dict[int, OnlineRolloutTask] = {}
            complete = [
                index
                for index, task in remaining.items()
                if task.output_path.is_file()
            ]
            for index in complete:
                remaining.pop(index)
                first_missing_seen_at.pop(index, None)
            for index, task in list(remaining.items()):
                if job_id is None:
                    continue
                status = query_slurm_task_status(job_id, index)
                missing_for = now - first_missing_seen_at.setdefault(index, now)
                if status is None:
                    if missing_for >= self.config.hpc.missing_task_grace_seconds:
                        retriable[index] = task
                    continue
                state = normalize_slurm_state(status.state)
                if state in _SLURM_PENDING_STATES or state in {
                    "COMPLETING",
                    "RUNNING",
                }:
                    if (
                        state == "RUNNING"
                        and walltime_seconds is not None
                        and status.elapsed_seconds is not None
                        and status.elapsed_seconds
                        >= walltime_seconds
                        + self.config.hpc.task_output_grace_seconds
                    ):
                        retriable[index] = task
                    continue
                if state == "TIMEOUT":
                    self._cleanup_timed_out_workspace(task, attempt)
                    if attempt < self.config.hpc.max_task_attempts:
                        retriable[index] = task
                        self.audit.write(
                            "online_hpc_rollout_timeout_retriable",
                            job_id=job_id,
                            attempt=attempt,
                            max_task_attempts=self.config.hpc.max_task_attempts,
                            task_index=index,
                            instance_id=task.case.instance_id,
                        )
                    else:
                        disposition = self._finalize_slurm_timeout(task, attempt)
                        remaining.pop(index, None)
                        first_missing_seen_at.pop(index, None)
                        self.audit.write(
                            (
                                "online_hpc_reflection_reviewer_timeout"
                                if disposition
                                == "reviewer_timeout_preserved_evaluator"
                                else "online_hpc_rollout_timeout_scored"
                            ),
                            job_id=job_id,
                            attempt=attempt,
                            max_task_attempts=self.config.hpc.max_task_attempts,
                            task_index=index,
                            instance_id=task.case.instance_id,
                            score=(
                                None
                                if disposition
                                == "reviewer_timeout_preserved_evaluator"
                                else 0.0
                            ),
                            terminal_reason=(
                                "reflection_reviewer_timeout"
                                if disposition
                                == "reviewer_timeout_preserved_evaluator"
                                else "slurm_timeout"
                            ),
                            timeout_disposition=disposition,
                        )
                    continue
                if state in _SLURM_TERMINAL_STATES:
                    retriable[index] = task
            for index in retriable:
                remaining.pop(index, None)
            if retriable:
                retriable_outputs.update(retriable)
                self.audit.write(
                    "online_hpc_rollout_missing_outputs_retriable",
                    job_id=job_id,
                    attempt=attempt,
                    task_indices=sorted(retriable),
                    instance_ids=[
                        retriable[index].case.instance_id for index in sorted(retriable)
                    ],
                )
            if remaining:
                self.sleep(self.config.hpc.poll_interval_seconds)
        return [retriable_outputs[index] for index in sorted(retriable_outputs)]


def _write_json(path: Path, value: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
