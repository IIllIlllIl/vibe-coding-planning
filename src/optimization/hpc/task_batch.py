"""Fingerprint-bound Slurm task batches shared by GEPA methods.

This module owns transport state only. Callers define task inputs, worker
commands, output schemas, and the meaning of a completed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import errno
import os
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Sequence

from src.exceptions import ControllerYield, confirmed_run_stop
from src.optimization.audit import optional_diagnostic
from src.artifact_policy import mark_artifact_path, mark_private_artifact_dir
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.slurm import (
    SlurmLookupUnavailable,
    SlurmSubmissionUnknown,
    SlurmSubmissionRejected,
    normalize_slurm_state,
    query_slurm_array_job_id,
    query_slurm_task_status,
    submit_slurm_array,
)


TERMINAL_STATES = {
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


@dataclass(frozen=True)
class TaskFiles:
    index: int
    instance_id: str
    manifest_path: Path
    output_path: Path
    attempts_dir: Path


class TaskBatchBlocked(RuntimeError):
    """An attempt cannot advance; retained evidence determines recovery."""

    def __init__(self, message, *, failure_record=None):
        super().__init__(message)
        self.failure_record = failure_record or {}


class TaskAttemptsExhausted(TaskBatchBlocked):
    """All configured fresh-Agent attempts ended without valid output."""


def retry_io(operation):
    """Retry the same storage operation, never the Agent that produced it."""
    for attempt in range(3):
        try:
            return operation()
        except OSError as exc:
            if exc.errno in {errno.ENOSPC, errno.EDQUOT} or attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))


def read_json(path: Path):
    return retry_io(lambda: json.loads(path.read_text(encoding="utf-8")))


def atomic_json(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")

    def save():
        mark_artifact_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)

    retry_io(save)


class SlurmTaskBatch:
    """Submit and recover a set of independent, atomic Agent tasks."""

    def __init__(
        self,
        hpc: HPCConfig,
        *,
        submitter: Callable[[Path], str | None] | None = None,
    ) -> None:
        self.hpc = hpc
        self.submitter = submitter or submit_slurm_array

    def run(
        self,
        *,
        batch_dir: Path,
        fingerprint: str,
        tasks: Sequence[TaskFiles],
        job_name: Callable[[int], str],
        write_script: Callable[[Sequence[int], int], Path],
        validate_output: Callable[[TaskFiles, dict[str, Any]], None],
        finalize_case_failure: (
            Callable[[TaskFiles, dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
    ) -> list[dict[str, Any]]:
        """Return validated outputs or yield after durable asynchronous work."""
        mark_private_artifact_dir(batch_dir)
        state_path = batch_dir / "task_state.json"
        state = self._load_or_create_state(
            state_path,
            fingerprint=fingerprint,
        )
        phase = str(state["phase"])
        reopen_reason: str | None = None
        if phase == "EXHAUSTED":
            # Callers finalize exhausted Agent outcomes. Only unfinished tasks
            # enter another recovery wave; attempt IDs never overwrite history.
            state["recovery_attempt_offset"] = int(state["active_attempt"])
            reopen_reason = "retry_incomplete_tasks"
        elif phase == "BLOCKED" and not confirmed_run_stop(state.get("terminal_failure", {})):
            reopen_reason = "retry_incomplete_tasks"
        if reopen_reason is not None:
            self._reopen_block(
                state_path,
                state,
                batch_dir=batch_dir,
                reason=reopen_reason,
            )
            phase = str(state["phase"])
        if phase in {"BLOCKED", "EXHAUSTED"}:
            error = str(
                state.get("terminal_failure", {}).get(
                    "error",
                    f"Slurm task batch is {phase.lower()}",
                )
            )
            error_type = (
                TaskAttemptsExhausted
                if phase == "EXHAUSTED"
                else TaskBatchBlocked
            )
            raise error_type(error, failure_record=state.get("terminal_failure"))

        attempt = int(state["active_attempt"])
        outputs, failed, blocking = self._inspect_outputs(
            tasks,
            validate_output,
            attempt=attempt,
            finalize_case_failure=finalize_case_failure,
        )
        self._reclaim_attempt_workspaces(
            [task for task in tasks if task.output_path.is_file()],
            attempt=attempt,
        )
        if blocking:
            instance_ids = ", ".join(
                task.instance_id for task, _ in blocking
            )
            details = [
                {
                    "instance_id": task.instance_id,
                    **failure,
                }
                for task, failure in blocking
            ]
            error = "blocking Slurm Agent task failure: " + instance_ids
            self._save_terminal_state(
                state_path,
                state,
                phase="BLOCKED",
                error=error,
                failure_kind="blocking_task_output",
                attempt=attempt,
                job_id=state.get("active_job_id"),
                details=details,
            )
            raise TaskBatchBlocked(error, failure_record=state.get("terminal_failure"))
        if len(outputs) == len(tasks):
            self._save_state(
                state_path,
                state,
                phase="COMPLETE",
                active_job_id=state.get("active_job_id"),
            )
            return [outputs[task.index] for task in tasks]

        job_id = state.get("active_job_id")
        if phase == "SUBMITTING" and not job_id:
            try:
                job_id = query_slurm_array_job_id(job_name(attempt))
            except SlurmLookupUnavailable as exc:
                state["submission_lookup_error"] = str(exc)
                job_id = None
            if job_id is None:
                submitting_since = float(
                    state.setdefault("submitting_since", time.time())
                )
                self._save_state(
                    state_path,
                    state,
                    phase="SUBMITTING",
                    active_job_id=None,
                )
                if (
                    time.time() - submitting_since
                    >= self.hpc.missing_task_grace_seconds
                ):
                    raise RuntimeError(
                        "Slurm submission outcome stayed ambiguous beyond "
                        "the configured grace window; no replacement job was submitted. "
                        + state.get("submission_lookup_error", state.get("submission_error", ""))
                    )
                raise ControllerYield(
                    batch_dir=str(batch_dir),
                    job_id=None,
                    reason="waiting_for_submission_reconciliation",
                )
            state.pop("submitting_since", None)
            state.pop("submission_lookup_error", None)
            self._save_state(
                state_path,
                state,
                phase="SUBMITTED",
                active_job_id=job_id,
            )
            raise ControllerYield(
                batch_dir=str(batch_dir),
                job_id=str(job_id),
                reason="waiting_for_reconciled_task_batch",
            )

        pending = [
            task for task in tasks
            if task.index not in outputs
        ]
        if failed and not job_id:
            # A rejected cached output still belongs to an earlier attempt,
            # even when its scheduler receipt is absent. Archive it before
            # another Worker may write this task's output path.
            attempt += 1
        if job_id:
            terminal_missing: list[TaskFiles] = []
            active: list[TaskFiles] = []
            missing_since = state.setdefault("missing_since", {})
            terminal_since = state.setdefault("terminal_since", {})
            now = time.time()
            for task in pending:
                key = str(task.index)
                if task in failed:
                    terminal_missing.append(task)
                    missing_since.pop(key, None)
                    terminal_since.pop(key, None)
                    continue
                status = query_slurm_task_status(str(job_id), task.index)
                if status is None:
                    terminal_since.pop(key, None)
                    first_missing = float(missing_since.setdefault(key, now))
                    if (
                        now - first_missing
                        >= self.hpc.missing_task_grace_seconds
                    ):
                        self._save_slurm_status(
                            task,
                            attempt=attempt,
                            job_id=str(job_id),
                            state="MISSING",
                            elapsed_seconds=None,
                            raw="",
                        )
                        terminal_missing.append(task)
                    else:
                        active.append(task)
                    continue
                missing_since.pop(key, None)
                normalized = normalize_slurm_state(status.state)
                if normalized == "CANCELLED":
                    raise TaskBatchBlocked("Slurm task cancelled; explicit restart required",
                        failure_record={"slurm_state": normalized, "job_id": str(job_id)})
                if normalized in TERMINAL_STATES:
                    self._save_slurm_status(
                        task,
                        attempt=attempt,
                        job_id=str(job_id),
                        state=normalized,
                        elapsed_seconds=status.elapsed_seconds,
                        raw=status.raw,
                    )
                    first_terminal = float(
                        terminal_since.setdefault(key, now)
                    )
                    if (
                        now - first_terminal
                        >= self.hpc.task_output_grace_seconds
                    ):
                        terminal_missing.append(task)
                    else:
                        active.append(task)
                else:
                    terminal_since.pop(key, None)
                    active.append(task)
            self._save_state(
                state_path,
                state,
                phase="SUBMITTED",
                active_job_id=str(job_id),
            )
            if active:
                raise ControllerYield(
                    batch_dir=str(batch_dir),
                    job_id=str(job_id),
                    reason="waiting_for_task_batch",
                )
            pending = terminal_missing
            attempt += 1

        self._reclaim_attempt_workspaces(pending, attempt=attempt - 1)
        if attempt > int(state.get("recovery_attempt_offset", 0)) + self.hpc.max_task_attempts:
            instance_ids = ", ".join(task.instance_id for task in pending)
            error = (
                "Slurm Agent tasks failed without valid atomic output after "
                f"{self.hpc.max_task_attempts} attempt(s): {instance_ids}"
            )
            self._save_terminal_state(
                state_path,
                state,
                phase="EXHAUSTED",
                error=error,
                failure_kind="task_attempts_exhausted",
                attempt=attempt - 1,
                job_id=str(job_id) if job_id else None,
                details=[
                    {
                        "instance_id": task.instance_id,
                        "output_path": str(task.output_path),
                    }
                    for task in pending
                ],
            )
            raise TaskAttemptsExhausted(error)
        if not self.hpc.submit:
            raise RuntimeError(
                f"Slurm task batch prepared at {batch_dir}; set hpc.submit=true"
            )

        script_path = write_script([task.index for task in pending], attempt)
        # A dependent preparation wave may yield here. Preserve the failed
        # output until dependencies are ready so resume still recognizes this
        # as a failed task, not an inexplicably missing output from the old job.
        self._archive_failed_outputs(batch_dir, pending, attempt - 1)
        if "submission_error" in state:
            state["last_submission_error"] = state.pop("submission_error")
        state.pop("submission_lookup_error", None)
        state["submitting_since"] = time.time()
        self._save_state(
            state_path,
            state,
            phase="SUBMITTING",
            active_attempt=attempt,
            active_job_id=None,
        )
        try:
            new_job_id = self.submitter(script_path)
        except SlurmSubmissionUnknown as exc:
            state["submission_error"] = str(exc)
            self._save_state(state_path, state, phase="SUBMITTING",
                             active_attempt=attempt, active_job_id=None)
            raise ControllerYield(batch_dir=str(batch_dir), job_id=None,
                                  reason="waiting_for_submission_reconciliation") from exc
        except SlurmSubmissionRejected as exc:
            self._save_terminal_state(state_path, state, phase="BLOCKED", error=str(exc),
                failure_kind="submission_rejected", attempt=attempt, job_id=None, details=[])
            raise TaskBatchBlocked(str(exc)) from exc
        if new_job_id:
            state.pop("submitting_since", None)
        self._save_state(
            state_path,
            state,
            phase="SUBMITTED" if new_job_id else "SUBMITTING",
            active_attempt=attempt,
            active_job_id=new_job_id,
        )
        raise ControllerYield(
            batch_dir=str(batch_dir),
            job_id=str(new_job_id) if new_job_id else None,
            reason="waiting_for_submitted_task_batch",
        )

    @staticmethod
    def _inspect_outputs(
        tasks: Sequence[TaskFiles],
        validate_output: Callable[[TaskFiles, dict[str, Any]], None],
        *,
        attempt: int,
        finalize_case_failure: (
            Callable[[TaskFiles, dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
    ) -> tuple[
        dict[int, dict[str, Any]],
        list[TaskFiles],
        list[tuple[TaskFiles, dict[str, Any]]],
    ]:
        outputs: dict[int, dict[str, Any]] = {}
        failed: list[TaskFiles] = []
        blocking: list[tuple[TaskFiles, dict[str, Any]]] = []
        for task in tasks:
            if not task.output_path.is_file():
                continue
            try:
                value = json.loads(task.output_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                SlurmTaskBatch._save_host_validation_failure(
                    task,
                    attempt=attempt,
                    stage="host_output_read",
                    exc=exc,
                )
                failed.append(task)
                continue
            if not isinstance(value, dict):
                SlurmTaskBatch._save_host_validation_failure(
                    task,
                    attempt=attempt,
                    stage="host_output_read",
                    exc=ValueError("atomic worker output must be a JSON object"),
                )
                failed.append(task)
                continue
            if value.get("status") != "completed" and finalize_case_failure is not None:
                finalized = finalize_case_failure(task, value)
                if finalized is not None:
                    outputs[task.index] = finalized
                    continue
            if (
                value.get("status") != "completed"
                and confirmed_run_stop(value)
            ):
                blocking.append(
                    (
                        task,
                        {
                            "failure_stage": "worker_execution",
                            "errno": value.get("errno"),
                            "error_type": str(
                                value.get("error_type", "WorkerBlockingFailure")
                            ),
                            "error": str(
                                value.get("error", "worker reported blocking failure")
                            ),
                            "output_path": str(task.output_path),
                        },
                    )
                )
                continue
            if value.get("status") != "completed":
                failed.append(task)
                continue
            try:
                validate_output(task, value)
            except Exception as exc:
                failure = SlurmTaskBatch._save_host_validation_failure(
                    task,
                    attempt=attempt,
                    stage="host_output_validation",
                    exc=exc,
                )
                if confirmed_run_stop({"error_type": type(exc).__name__, "errno": getattr(exc, "errno", None)}):
                    blocking.append((task, {**failure, "errno": getattr(exc, "errno", None)}))
                else:
                    failed.append(task)
                continue
            outputs[task.index] = value
        return outputs, failed, blocking

    @staticmethod
    def _reopen_block(
        state_path: Path,
        state: dict[str, Any],
        *,
        batch_dir: Path,
        reason: str,
    ) -> None:
        evidence_path = batch_dir / "operational_reclassifications.jsonl"
        record = {
            "schema_version": 1,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "prior_state": dict(state),
            "new_phase": "SUBMITTED",
            "new_active_job_id": state.get("last_job_id"),
            "controller_project_git_head": os.environ.get(
                "VIBE_CONTROLLER_GIT_HEAD"
            ),
        }
        with optional_diagnostic("historical task reclassification log"):
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            with evidence_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        state.pop("terminal_failure", None)
        SlurmTaskBatch._save_state(
            state_path,
            state,
            phase="SUBMITTED",
            active_attempt=int(state["active_attempt"]),
            active_job_id=(
                str(state["last_job_id"]) if state.get("last_job_id") else None
            ),
        )

    @staticmethod
    def _save_host_validation_failure(
        task: TaskFiles,
        *,
        attempt: int,
        stage: str,
        exc: Exception,
    ) -> dict[str, Any]:
        failure = {
            "schema_version": 1,
            "failure_stage": stage,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "task_index": task.index,
            "instance_id": task.instance_id,
            "attempt": attempt,
            "output_path": str(task.output_path),
        }
        atomic_json(
            task.attempts_dir
            / f"attempt_{attempt:02d}"
            / "host_validation_failure.json",
            failure,
        )
        return failure

    @staticmethod
    def _save_slurm_status(
        task: TaskFiles,
        *,
        attempt: int,
        job_id: str,
        state: str,
        elapsed_seconds: int | None,
        raw: str,
    ) -> None:
        atomic_json(
            task.attempts_dir
            / f"attempt_{attempt:02d}"
            / "slurm_status.json",
            {
                "schema_version": 1,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "job_id": job_id,
                "task_index": task.index,
                "instance_id": task.instance_id,
                "state": state,
                "elapsed_seconds": elapsed_seconds,
                "raw": raw,
            },
        )

    @staticmethod
    def _save_terminal_state(
        path: Path,
        state: dict[str, Any],
        *,
        phase: str,
        error: str,
        failure_kind: str,
        attempt: int,
        job_id: str | None,
        details: list[dict[str, Any]],
    ) -> None:
        state["last_job_id"] = job_id
        state["terminal_failure"] = {
            "failure_kind": failure_kind,
            "error": error,
            "attempt": attempt,
            "details": details,
        }
        SlurmTaskBatch._save_state(
            path,
            state,
            phase=phase,
            active_attempt=attempt,
            active_job_id=None,
        )

    @staticmethod
    def _archive_failed_outputs(
        batch_dir: Path,
        tasks: Sequence[TaskFiles],
        previous_attempt: int,
    ) -> None:
        if previous_attempt < 1:
            return
        archive = batch_dir / "failed_outputs" / f"attempt_{previous_attempt:02d}"
        for task in tasks:
            if not task.output_path.exists():
                continue
            archive.mkdir(parents=True, exist_ok=True)
            archived = archive / task.output_path.name
            shutil.move(str(task.output_path), str(archived))
            diagnostic = task.attempts_dir / f"attempt_{previous_attempt:02d}" / "host_validation_failure.json"
            if diagnostic.is_file():
                # Preserve the rejected output verbatim. A separate copy of
                # the existing diagnostic supplies exact feedback on retry.
                try:
                    shutil.copyfile(diagnostic, archived.with_suffix(".host_validation.json"))
                except OSError:
                    pass

    @staticmethod
    def _reclaim_attempt_workspaces(
        tasks: Sequence[TaskFiles],
        *,
        attempt: int,
    ) -> None:
        """Best-effort cleanup of exact disposable workspaces after a task stops."""

        if attempt < 1:
            return
        for task in tasks:
            path = task.attempts_dir / f"attempt_{attempt:02d}" / "workspaces"
            try:
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            except OSError:
                # Cleanup is operational and may not replace scientific task
                # output or retry state. The Supervisor repeats this exact,
                # bounded reclamation once all remote jobs have stopped.
                continue

    @staticmethod
    def _load_or_create_state(
        path: Path,
        *,
        fingerprint: str,
    ) -> dict[str, Any]:
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("fingerprint") != fingerprint:
                raise RuntimeError("Slurm task batch fingerprint mismatch")
            return state
        state = {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "phase": "PREPARED",
            "active_attempt": 1,
            "active_job_id": None,
            "missing_since": {},
            "terminal_since": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json(path, state)
        return state

    @staticmethod
    def _save_state(
        path: Path,
        state: dict[str, Any],
        *,
        phase: str,
        active_attempt: int | None = None,
        active_job_id: str | None = None,
    ) -> None:
        state.update(
            phase=phase,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        if active_attempt is not None:
            state["active_attempt"] = active_attempt
        state["active_job_id"] = active_job_id
        atomic_json(path, state)
