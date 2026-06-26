from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

from scripts.hpc_preheat_watchdog_lib.classifier import classify_failure
from scripts.hpc_preheat_watchdog_lib.config import WatchdogConfig
from scripts.hpc_preheat_watchdog_lib.preheat import expected_image_count, submit_preheat
from scripts.hpc_preheat_watchdog_lib.repair import (
    changed_files,
    restore_new_disallowed_changes,
    run_agent_repair,
    whitelist_violations,
)
from scripts.hpc_preheat_watchdog_lib.slurm import SlurmClient, is_terminal
from scripts.hpc_preheat_watchdog_lib.state import WatchdogState
from scripts.hpc_preheat_watchdog_lib.validators import run_validations


class SlurmLike(Protocol):
    def get_job_state(self, job_id: str) -> str: ...
    def read_logs(self, stdout_path: str | None, stderr_path: str | None) -> str: ...
    def cache_status(self, cache_dir: str, expected: int) -> dict: ...


def initialize_expected_counts(config: WatchdogConfig, state: WatchdogState) -> None:
    if state.expected_pilot_images == 0:
        state.expected_pilot_images = expected_image_count(config.pilot_config)
    if state.expected_full_images == 0:
        state.expected_full_images = expected_image_count(config.full_config)


def _submit_pilot(config: WatchdogConfig, state: WatchdogState) -> None:
    job = submit_preheat(config, role="pilot")
    state.pilot_job_id = job.job_id
    state.pilot_stdout_path = job.stdout_path
    state.pilot_stderr_path = job.stderr_path
    state.phase = "pilot_waiting"
    state.touch_action()


def _submit_full(config: WatchdogConfig, state: WatchdogState) -> None:
    job = submit_preheat(config, role="full")
    state.full_job_id = job.job_id
    state.full_stdout_path = job.stdout_path
    state.full_stderr_path = job.stderr_path
    state.phase = "full_preheat_submitted"
    state.touch_action()


def _pilot_completed(slurm: SlurmLike, config: WatchdogConfig, state: WatchdogState) -> bool:
    status = slurm.cache_status(config.pilot_sif_cache_dir, state.expected_pilot_images)
    state.last_sif_count = int(status.get("sif_count") or 0)
    return bool(status.get("complete"))


def _handle_pilot_failure(config: WatchdogConfig, state: WatchdogState, logs: str) -> None:
    classification = classify_failure(logs)
    state.last_error_class = classification.error_class
    state.last_error = logs[-4000:]
    if classification.agent_quota:
        if state.agent_cooldowns >= config.max_agent_cooldowns:
            state.phase = "blocked"
            return
        state.agent_cooldowns += 1
        until = datetime.now(timezone.utc) + timedelta(seconds=config.agent_cooldown_seconds)
        state.cooldown_until = until.isoformat()
        state.phase = "agent_cooldown"
        return
    if not classification.repairable or not config.enable_agent_repair:
        state.phase = "blocked"
        return
    if state.repair_attempts >= config.max_repair_attempts:
        state.phase = "blocked"
        return

    before = set(changed_files())
    state.repair_attempts += 1
    result = run_agent_repair(config, error_class=classification.error_class, logs=logs)
    if result.agent_quota:
        _handle_pilot_failure(config, state, result.output)
        return
    after = set(changed_files())
    violations = whitelist_violations(before, after)
    if violations:
        state.whitelist_violations += 1
        restore_new_disallowed_changes(violations)
        if any(item.preexisting for item in violations) or (
            state.whitelist_violations > config.max_whitelist_violations
        ):
            state.phase = "blocked"
            state.last_error = "repair modified disallowed preexisting files"
            return
        state.phase = "pilot_failed"
        state.last_error = "repair modified disallowed files; restored and will retry"
        return
    validation = run_validations()
    if not result.ok or not validation.ok:
        state.phase = "blocked"
        state.last_error = (result.output + "\n" + validation.output)[-4000:]
        return
    state.phase = "idle"
    state.pilot_job_id = None
    state.pilot_stdout_path = None
    state.pilot_stderr_path = None


def _cooldown_elapsed(state: WatchdogState) -> bool:
    if not state.cooldown_until:
        return True
    return datetime.fromisoformat(state.cooldown_until) <= datetime.now(timezone.utc)


def run_once(config: WatchdogConfig, state: WatchdogState, slurm: SlurmLike) -> WatchdogState:
    initialize_expected_counts(config, state)
    state.touch_checked()

    if state.phase == "idle":
        if config.submit:
            _submit_pilot(config, state)
        return state

    if state.phase == "pilot_waiting":
        if not state.pilot_job_id:
            state.phase = "idle"
            return state
        job_state = slurm.get_job_state(state.pilot_job_id)
        state.last_job_state = job_state
        if not is_terminal(job_state):
            return state
        logs = slurm.read_logs(state.pilot_stdout_path, state.pilot_stderr_path)
        if job_state == "COMPLETED" and _pilot_completed(slurm, config, state):
            state.phase = "pilot_completed"
        else:
            state.phase = "pilot_failed"
            state.last_error = logs[-4000:]
        return state

    if state.phase == "pilot_failed":
        logs = state.last_error or slurm.read_logs(state.pilot_stdout_path, state.pilot_stderr_path)
        _handle_pilot_failure(config, state, logs)
        return state

    if state.phase == "agent_cooldown":
        if _cooldown_elapsed(state):
            state.cooldown_until = None
            state.phase = "pilot_failed"
        return state

    if state.phase == "pilot_completed":
        if config.submit:
            _submit_full(config, state)
        return state

    if state.phase == "full_preheat_submitted":
        if config.stop_after_full_submit or not state.full_job_id:
            return state
        job_state = slurm.get_job_state(state.full_job_id)
        state.last_job_state = job_state
        if job_state == "COMPLETED":
            status = slurm.cache_status(config.full_sif_cache_dir, state.expected_full_images)
            state.last_sif_count = int(status.get("sif_count") or 0)
            if status.get("complete"):
                state.phase = "completed"
        elif is_terminal(job_state):
            state.phase = "blocked"
        return state

    return state


def run_forever(config: WatchdogConfig, state: WatchdogState, slurm: SlurmClient, save) -> int:
    while True:
        state = run_once(config, state, slurm)
        save(state)
        if state.phase in {"completed", "blocked"}:
            return 0 if state.phase == "completed" else 2
        if state.phase == "full_preheat_submitted" and config.stop_after_full_submit:
            return 0
        time.sleep(config.poll_interval_seconds)
