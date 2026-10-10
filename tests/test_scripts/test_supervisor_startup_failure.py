from contextlib import nullcontext
from types import SimpleNamespace

import pytest

import scripts.hpc_resume_loop as loop


@pytest.mark.parametrize("slurm_state,expected", [
    ("FAILED", "startup_failed"), ("OUT_OF_MEMORY", "startup_failed"),
    ("COMPLETED", "startup_failed"), ("CANCELLED", "startup_failed"),
    ("UNKNOWN", "startup_waiting"), ("PENDING", "startup_waiting"),
    ("RUNNING", "startup_waiting"), ("NODE_FAIL", "startup_retryable"),
    ("BOOT_FAIL", "startup_retryable"), ("PREEMPTED", "startup_retryable"),
    ("TIMEOUT", "startup_retryable"),
])
def test_startup_classification(monkeypatch, slurm_state, expected):
    monkeypatch.setattr(loop, "get_job_state", lambda config, job: slurm_state)
    report = loop.startup_controller_status(SimpleNamespace(remote_staging_root="/scratch/staging/run"),
                                           {"last_controller_job_id": "123"})
    assert report["state"] == expected
    assert report["stderr_path"].endswith("job_123.err")
    assert report["action"]


def test_infrastructure_retry_is_bounded(monkeypatch):
    monkeypatch.setattr(loop, "get_job_state", lambda config, job: "NODE_FAIL")
    assert loop.startup_controller_status(SimpleNamespace(remote_staging_root=None),
        {"last_controller_job_id": "123", "startup_failure_recoveries": 1})["state"] == "startup_failed"


def test_unknown_submission_identity_blocks():
    assert loop.startup_controller_status(None, {})["error_type"] == "MissingControllerJobIdentity"


def test_ssh_failure_never_authorizes_submission(monkeypatch):
    def fail(*args):
        raise TimeoutError("status unavailable")
    monkeypatch.setattr(loop, "get_job_state", fail)
    assert loop.startup_controller_status(None, {"last_controller_job_id": "123"})["state"] == "startup_waiting"


def test_failed_controller_without_manifest_stops_before_cleanup_or_submission(monkeypatch):
    config = SimpleNamespace(submit=True, slice_time="00:08:00", poll_interval_seconds=0,
        target_iterations=1, max_runs=20, remote_run_snapshot="run", remote_staging_root="/scratch/run")
    state = {"submissions": 1, "last_controller_job_id": "123"}
    monkeypatch.setattr(loop, "_supervisor_lock", lambda config: nullcontext())
    monkeypatch.setattr(loop, "_load_supervisor_state", lambda config: state)
    monkeypatch.setattr(loop, "_submission_identity_error", lambda config: None)
    monkeypatch.setattr(loop, "_save_supervisor_state", lambda config, state: None)
    monkeypatch.setattr(loop, "remote_run_status", lambda config: {"state": "missing"})
    monkeypatch.setattr(loop, "get_job_state", lambda config, job: "FAILED")
    monkeypatch.setattr(loop, "submit_slice", lambda config: pytest.fail("duplicate submission"))
    monkeypatch.setattr(loop, "reclaim_remote_staging", lambda config: pytest.fail("must preserve diagnosis"))
    assert loop.run_loop(config) == 1
    assert state["status"] == "blocked_controller_startup"
    assert state["submissions"] == 1


@pytest.mark.parametrize("slurm_state", ["UNKNOWN", "PENDING", "RUNNING"])
def test_invisible_or_active_controller_waits_without_cleanup_or_submission(monkeypatch, slurm_state):
    config = SimpleNamespace(submit=True, slice_time="00:08:00", poll_interval_seconds=0,
        target_iterations=1, max_runs=20, remote_run_snapshot="run", remote_staging_root=None, once=True)
    state = {"submissions": 1, "last_controller_job_id": "123"}
    monkeypatch.setattr(loop, "_supervisor_lock", lambda config: nullcontext())
    monkeypatch.setattr(loop, "_load_supervisor_state", lambda config: state)
    monkeypatch.setattr(loop, "_submission_identity_error", lambda config: None)
    monkeypatch.setattr(loop, "_save_supervisor_state", lambda config, state: None)
    monkeypatch.setattr(loop, "remote_run_status", lambda config: {"state": "missing"})
    monkeypatch.setattr(loop, "get_job_state", lambda config, job: slurm_state)
    monkeypatch.setattr(loop, "submit_slice", lambda config: pytest.fail("duplicate submission"))
    monkeypatch.setattr(loop, "reclaim_remote_staging", lambda config: pytest.fail("premature cleanup"))
    assert loop.run_loop(config) == 0
    assert state["status"] == "waiting_controller_initialization"
    assert state["submissions"] == 1
