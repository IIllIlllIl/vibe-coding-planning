"""No-scheduler regression of accepted submissions with lost receipts."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.exceptions import ControllerYield, PermanentProviderError
from src.optimization.hpc import slurm, task_batch
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskBatchBlocked, TaskFiles, atomic_json
from src.optimization.hpc.worker_contract import worker_failure


@pytest.mark.parametrize("detail", ["Socket timed out on send/recv operation", "Connection reset by peer"])
def test_rpc_failure_is_an_unknown_submission_not_a_rejection(monkeypatch, detail):
    monkeypatch.setattr(slurm.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr=detail))
    with pytest.raises(slurm.SlurmSubmissionUnknown):
        slurm.submit_slurm_array(Path("synthetic.sbatch"))


def test_local_rpc_deadline_has_unknown_outcome(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args[0], 60)
    monkeypatch.setattr(slurm.subprocess, "run", timeout)
    with pytest.raises(slurm.SlurmSubmissionUnknown):
        slurm.submit_slurm_array(Path("synthetic.sbatch"))


def test_invalid_partition_is_still_a_definite_error(monkeypatch):
    monkeypatch.setattr(slurm.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr="Invalid partition name"))
    with pytest.raises(RuntimeError) as caught:
        slurm.submit_slurm_array(Path("synthetic.sbatch"))
    assert not isinstance(caught.value, slurm.SlurmSubmissionUnknown)


def test_a_returned_receipt_is_not_discarded_with_nonzero_client_exit(monkeypatch):
    monkeypatch.setattr(slurm.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="Submitted batch job 123\n", stderr="connection closed"))
    assert slurm.submit_slurm_array(Path("synthetic.sbatch")) == "123"


def test_unrecognized_client_failure_remains_unknown(monkeypatch):
    monkeypatch.setattr(slurm.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr="unexpected client failure"))
    with pytest.raises(slurm.SlurmSubmissionUnknown):
        slurm.submit_slurm_array(Path("synthetic.sbatch"))


@pytest.mark.parametrize("responses,expected", [
    ([(0, "123\n123_0\n", "")], "123"),
    ([(0, "", ""), (0, "123\n123_0\n", "")], "123"),
    ([(0, "", ""), (0, "", "")], None),
])
def test_reconciliation_uses_active_or_accounted_array_identity(monkeypatch, responses, expected):
    values = iter(responses)
    def query(args):
        rc, out, err = next(values)
        return {"returncode": rc, "stdout": out, "stderr": err}
    monkeypatch.setattr(slurm, "_run_optional_command", query)
    assert slurm.query_slurm_array_job_id("unique-job") == expected


@pytest.mark.parametrize("responses", [
    [(0, "", ""), (1, "", "database unavailable")],
    [(1, "", "controller unavailable"), (0, "", "")],
    [(0, "123\n124\n", "")],
    [(0, "", ""), (0, "123\n124\n", "")],
])
def test_unavailable_or_ambiguous_lookup_is_not_an_absent_job(monkeypatch, responses):
    values = iter(responses)
    def query(args):
        rc, out, err = next(values)
        return {"returncode": rc, "stdout": out, "stderr": err}
    monkeypatch.setattr(slurm, "_run_optional_command", query)
    with pytest.raises(slurm.SlurmLookupUnavailable):
        slurm.query_slurm_array_job_id("unique-job")


def _batch(tmp_path, *, submitter=None):
    task = TaskFiles(0, "case", tmp_path / "input.json", tmp_path / "output.json", tmp_path / "attempts")
    calls = []
    def unknown(script):
        calls.append(script)
        raise slurm.SlurmSubmissionUnknown("synthetic lost receipt")
    runtime = SlurmTaskBatch(HPCConfig(submit=True, missing_task_grace_seconds=30), submitter=submitter or unknown)
    kwargs = dict(batch_dir=tmp_path, fingerprint="same-frozen-input", tasks=[task],
        job_name=lambda attempt: f"unique-job-a{attempt}",
        write_script=lambda indices, attempt: tmp_path / f"attempt_{attempt}.sbatch",
        validate_output=lambda task, output: None)
    return runtime, kwargs, task, calls


def test_lost_receipt_yields_then_recovers_without_resubmitting(tmp_path, monkeypatch):
    runtime, kwargs, task, calls = _batch(tmp_path)
    with pytest.raises(ControllerYield) as caught:
        runtime.run(**kwargs)
    assert caught.value.reason == "waiting_for_submission_reconciliation"
    state = json.loads((tmp_path / "task_state.json").read_text())
    assert state["phase"] == "SUBMITTING" and state["active_attempt"] == 1
    assert state["submission_error"] == "synthetic lost receipt"
    monkeypatch.setattr(task_batch, "query_slurm_array_job_id", lambda name: "123")
    with pytest.raises(ControllerYield) as caught:
        runtime.run(**kwargs)
    assert caught.value.reason == "waiting_for_reconciled_task_batch"
    assert json.loads((tmp_path / "task_state.json").read_text())["active_job_id"] == "123"
    atomic_json(task.output_path, {"status": "completed", "instance_id": "case", "original": "raw\n"})
    original = task.output_path.read_bytes()
    assert runtime.run(**kwargs)[0]["original"] == "raw\n"
    assert len(calls) == 1 and task.output_path.read_bytes() == original


def test_completed_output_wins_even_when_scheduler_lookup_is_unavailable(tmp_path, monkeypatch):
    runtime, kwargs, task, calls = _batch(tmp_path)
    with pytest.raises(ControllerYield):
        runtime.run(**kwargs)
    atomic_json(task.output_path, {"status": "completed", "instance_id": "case"})
    monkeypatch.setattr(task_batch, "query_slurm_array_job_id", lambda name: pytest.fail("must reuse completed output"))
    assert runtime.run(**kwargs)[0]["status"] == "completed"
    assert len(calls) == 1


def test_unavailable_accounting_waits_with_bound_and_never_retries_submission(tmp_path, monkeypatch):
    runtime, kwargs, task, calls = _batch(tmp_path)
    monkeypatch.setattr(task_batch.time, "time", lambda: 10)
    with pytest.raises(ControllerYield):
        runtime.run(**kwargs)
    def unavailable(name):
        raise slurm.SlurmLookupUnavailable("synthetic accounting outage")
    monkeypatch.setattr(task_batch, "query_slurm_array_job_id", unavailable)
    monkeypatch.setattr(task_batch.time, "time", lambda: 20)
    with pytest.raises(ControllerYield):
        runtime.run(**kwargs)
    state = json.loads((tmp_path / "task_state.json").read_text())
    assert state["active_attempt"] == 1 and "accounting outage" in state["submission_lookup_error"]
    monkeypatch.setattr(task_batch.time, "time", lambda: 41)
    with pytest.raises(RuntimeError, match="no replacement job was submitted"):
        runtime.run(**kwargs)
    assert len(calls) == 1


def test_permanent_provider_failure_blocks_batch_without_retry_or_scored_outcome(tmp_path):
    runtime, kwargs, task, calls = _batch(tmp_path)
    failure = worker_failure({"instance_id": "case"}, "started", "agent_execution",
                             PermanentProviderError("Reauthentication required"))
    atomic_json(task.output_path, failure)
    with pytest.raises(TaskBatchBlocked):
        runtime.run(**kwargs)
    assert not calls
    assert "task_outcome" not in failure and "agent_output" not in failure
    assert json.loads((tmp_path / "task_state.json").read_text())["phase"] == "BLOCKED"


def test_definite_rejection_persists_blocked_state_without_reconciliation(tmp_path, monkeypatch):
    def reject(script):
        raise slurm.SlurmSubmissionRejected("Invalid partition name")
    runtime, kwargs, task, calls = _batch(tmp_path, submitter=reject)
    monkeypatch.setattr(task_batch, "query_slurm_array_job_id", lambda name: pytest.fail("rejection is definite"))
    with pytest.raises(TaskBatchBlocked, match="Invalid partition"):
        runtime.run(**kwargs)
    state = json.loads((tmp_path / "task_state.json").read_text())
    assert state["phase"] == "BLOCKED" and state["terminal_failure"]["failure_kind"] == "submission_rejected"
    with pytest.raises(TaskBatchBlocked):
        runtime.run(**kwargs)
