"""Fingerprint-bound Slurm task batches shared by GEPA methods.

This module owns transport state only. Callers define task inputs, worker
commands, output schemas, and the meaning of a completed result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable, Sequence

from src.exceptions import ControllerYield
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.slurm import (
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


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


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
    ) -> list[dict[str, Any]]:
        """Return validated outputs or yield after durable asynchronous work."""
        state_path = batch_dir / "task_state.json"
        state = self._load_or_create_state(
            state_path,
            fingerprint=fingerprint,
        )
        outputs, failed, blocking = self._inspect_outputs(tasks, validate_output)
        if blocking:
            instance_ids = ", ".join(task.instance_id for task in blocking)
            raise RuntimeError(
                "blocking Slurm Agent task failure: " + instance_ids
            )
        if len(outputs) == len(tasks):
            self._save_state(
                state_path,
                state,
                phase="COMPLETE",
                active_job_id=state.get("active_job_id"),
            )
            return [outputs[task.index] for task in tasks]

        attempt = int(state["active_attempt"])
        job_id = state.get("active_job_id")
        phase = str(state["phase"])
        if phase == "SUBMITTING" and not job_id:
            job_id = query_slurm_array_job_id(job_name(attempt))
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
                        "the configured grace window"
                    )
                raise ControllerYield(
                    batch_dir=str(batch_dir),
                    job_id=None,
                    reason="waiting_for_submission_reconciliation",
                )
            state.pop("submitting_since", None)
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
                        terminal_missing.append(task)
                    else:
                        active.append(task)
                    continue
                missing_since.pop(key, None)
                normalized = normalize_slurm_state(status.state)
                if normalized in TERMINAL_STATES:
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

        if attempt > self.hpc.max_task_attempts:
            instance_ids = ", ".join(task.instance_id for task in pending)
            raise RuntimeError(
                "Slurm Agent tasks failed without valid atomic output after "
                f"{self.hpc.max_task_attempts} attempt(s): {instance_ids}"
            )
        if not self.hpc.submit:
            raise RuntimeError(
                f"Slurm task batch prepared at {batch_dir}; set hpc.submit=true"
            )

        self._archive_failed_outputs(batch_dir, pending, attempt - 1)
        script_path = write_script([task.index for task in pending], attempt)
        self._save_state(
            state_path,
            state,
            phase="SUBMITTING",
            active_attempt=attempt,
            active_job_id=None,
        )
        new_job_id = self.submitter(script_path)
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
    ) -> tuple[
        dict[int, dict[str, Any]],
        list[TaskFiles],
        list[TaskFiles],
    ]:
        outputs: dict[int, dict[str, Any]] = {}
        failed: list[TaskFiles] = []
        blocking: list[TaskFiles] = []
        for task in tasks:
            if not task.output_path.is_file():
                continue
            try:
                value = json.loads(task.output_path.read_text(encoding="utf-8"))
                if value.get("status") == "blocking_failed":
                    blocking.append(task)
                    continue
                if value.get("status") != "completed":
                    failed.append(task)
                    continue
                validate_output(task, value)
            except Exception:
                failed.append(task)
                continue
            outputs[task.index] = value
        return outputs, failed, blocking

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
            shutil.move(str(task.output_path), str(archive / task.output_path.name))

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
