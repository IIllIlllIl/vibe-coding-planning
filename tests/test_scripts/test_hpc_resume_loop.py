from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.hpc_resume_loop as resume_loop
import scripts.hpc_supervisor_service as supervisor_service
from scripts.hpc_resume_loop import (
    AION_REMOTE_EMBEDDED_PYTHON,
    REMOTE_EMBEDDED_PYTHON,
    _remote_embedded_python,
    _remote_run_snapshot,
    _repo_relative,
    _with_default_remote_paths,
    reclaim_remote_workspaces,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "hpc_supervisor_service.py"
SEQUENCE_SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_sequence.py"


def test_submit_slice_retains_job_id_when_console_is_broken(monkeypatch):
    submissions = []
    monkeypatch.setattr(resume_loop, "_batch_command", lambda *args, **kwargs: ["fake-submit"])
    def submit(command):
        submissions.append(command)
        return subprocess.CompletedProcess(command, 0, '{"job_id": "12345"}', "warning")
    monkeypatch.setattr(resume_loop, "run_command", submit)
    def broken_console(*args, **kwargs):
        raise BrokenPipeError("diagnostic stdout unavailable")
    monkeypatch.setattr("builtins.print", broken_console)
    assert resume_loop.submit_slice(SimpleNamespace()) == "12345"
    assert submissions == [["fake-submit"]]


@pytest.mark.parametrize("blocked_log,missing_checkout", [(False, False), (True, False), (True, True)])
def test_supervisor_log_is_optional_but_checkout_is_required(tmp_path, monkeypatch, blocked_log, missing_checkout):
    """Execute only a harmless shell stub, never tmux, a model or Slurm."""
    root = tmp_path / "checkout"
    if not missing_checkout:
        root.mkdir()
    log_path = tmp_path / "log-target"
    if blocked_log:
        log_path.mkdir()  # Cannot redirect output to a directory.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    counter = tmp_path / "executions"
    stub = fake_bin / "caffeinate"
    stub.write_text("#!/bin/sh\nprintf 'once\\n' >> " + shlex.quote(str(counter)) + "\nexit 7\n")
    stub.chmod(0o755)
    monkeypatch.setattr(supervisor_service, "REPO_ROOT", root)
    monkeypatch.setattr(supervisor_service, "_has_session", lambda session: False)
    monkeypatch.setattr(supervisor_service, "mark_private_artifact_dir", lambda path: None)
    commands = []
    monkeypatch.setattr(supervisor_service, "_run", lambda command:
                        commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    assert supervisor_service.main(["start", "--session", "synthetic", "--log", str(log_path),
                                    "--gepa-config", "not-executed.yaml"]) == 0
    assert len(commands) == 1
    result = subprocess.run(["/bin/bash", "-c", commands[0][-1]], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"})
    if missing_checkout:
        assert result.returncode != 0
        assert not counter.exists()
    else:
        assert result.returncode == 7
        assert counter.read_text() == "once\n"


@pytest.mark.parametrize("cleanup", ["staging", "workspace"])
@pytest.mark.parametrize("completed", [True, False])
def test_optional_cleanup_failure_preserves_completed_run(monkeypatch, cleanup, completed):
    """Policy acceptance, not an assertion that today's hard stop is correct."""
    state = {"submissions": 1}
    config = SimpleNamespace(submit=True, slice_time="00:08:00", poll_interval_seconds=0,
        target_iterations=0, remote_run_snapshot="synthetic-private-run",
        once=True)
    monkeypatch.setattr(resume_loop, "_supervisor_lock", lambda config: nullcontext())
    monkeypatch.setattr(resume_loop, "_load_supervisor_state", lambda config: state)
    monkeypatch.setattr(resume_loop, "_save_supervisor_state", lambda *args: None)
    monkeypatch.setattr(resume_loop, "_submission_identity_error", lambda config: None)
    remote_status = {"state": "result" if completed else "resumable",
                     "status": "completed" if completed else "running",
                     "active_controllers": [], "active_workers": []}
    monkeypatch.setattr(resume_loop, "remote_run_status", lambda config: remote_status)
    def reclaim(name):
        def call(*args, **kwargs):
            if cleanup == name:
                raise OSError("synthetic cleanup-only failure")
            return {"removed": 0}
        return call
    monkeypatch.setattr(resume_loop, "reclaim_remote_staging", reclaim("staging"))
    monkeypatch.setattr(resume_loop, "reclaim_remote_workspaces", reclaim("workspace"))
    submissions = []
    monkeypatch.setattr(resume_loop, "submit_slice", lambda config: submissions.append(config) or "synthetic-job")
    result = resume_loop.run_loop(config)
    assert result == 0
    assert state["status"] == ("completed" if completed else "controller_submitted")
    assert len(submissions) == (0 if completed else 1)
    assert state[f"{cleanup}_reclamation_error"] == "synthetic cleanup-only failure"


def test_embedded_remote_modules_use_modern_iris_python() -> None:
    assert REMOTE_EMBEDDED_PYTHON == "python3.11"


def test_embedded_remote_modules_use_aion_login_python() -> None:
    class Config:
        ssh_target = "twang@access-aion.uni.lu"

    assert _remote_embedded_python(Config()) == AION_REMOTE_EMBEDDED_PYTHON
    assert AION_REMOTE_EMBEDDED_PYTHON.endswith("/bin/python3")


def test_embedded_remote_modules_keep_iris_login_python() -> None:
    class Config:
        ssh_target = "twang@access-iris.uni.lu"

    assert _remote_embedded_python(Config()) == REMOTE_EMBEDDED_PYTHON


def test_remote_status_short_circuits_a_missing_run_without_child_probes(
    monkeypatch, tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    squeue = fake_bin / "squeue"
    squeue.write_text("#!/bin/sh\nprintf '123|RUNNING\\n'\n", encoding="utf-8")
    squeue.chmod(0o755)
    config = SimpleNamespace(
        remote_run_snapshot=str(tmp_path / "not-created"),
        job_name="checker-smoke",
        ssh_port="8022",
        ssh_key="",
        ssh_target="tester@example.invalid",
    )

    def execute_embedded_script(command, *, check=False, timeout_seconds=None):
        assert timeout_seconds == 60
        arguments = shlex.split(command[-1].split("; " + REMOTE_EMBEDDED_PYTHON + " -c ", 1)[1])
        environment = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, "-c", *arguments],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        return subprocess.CompletedProcess(
            command, result.returncode, result.stdout, result.stderr
        )

    monkeypatch.setattr(resume_loop, "run_command", execute_embedded_script)
    assert resume_loop.remote_run_status(config) == {
        "state": "missing",
        "run_dir": config.remote_run_snapshot,
        "active_controllers": ["123|RUNNING"],
        "worker_states": {},
        "active_workers": [],
    }


def test_remote_status_timeout_is_retryable_without_submitting(monkeypatch) -> None:
    config = SimpleNamespace(
        remote_run_snapshot="/scratch/test/missing-run",
        job_name="checker-smoke",
        ssh_port="8022",
        ssh_key="",
        ssh_target="tester@example.invalid",
    )

    def timed_out(command, *, check=False, timeout_seconds=None):
        assert timeout_seconds == 60
        raise subprocess.TimeoutExpired(command, timeout_seconds)

    monkeypatch.setattr(resume_loop, "run_command", timed_out)
    assert resume_loop.remote_run_status(config) == {
        "state": "status_check_failed",
        "error_type": "remote_status_timeout",
        "stderr": "read-only remote status check exceeded 60 seconds",
    }


@pytest.mark.parametrize("search_finished", [False, True])
def test_remote_status_queries_only_authoritative_batch_and_window_jobs(tmp_path, monkeypatch, capsys, search_finished):
    (tmp_path / "iteration_progress.json").write_text(json.dumps({"completed_iterations": 20,
        "first_observed_completed_iterations": 0,
        "last_event": "optimization_completed" if search_finished else "state_saved"}))
    states = {
        "hpc_tasks/reflector/ordinary/task_state.json": {"phase": "SUBMITTED", "active_job_id": "123"},
        "hpc_tasks/curator/completed/task_state.json": {"phase": "COMPLETE", "active_job_id": "999"},
        "hpc_tasks/pce/fp/execution_windows/window_0000_0008/task_state.json": {
            "phase": "SUBMITTED", "active_job_id": "456"},
        "hpc_tasks/pce/fp/attempts/task_0000/attempt_01/workspaces/repo/task_state.json": {
            "phase": "SUBMITTED", "active_job_id": "777"},
    }
    for relative, state in states.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    config = SimpleNamespace(remote_run_snapshot=str(tmp_path), job_name="frozen-run",
                             ssh_port="8022", ssh_key="", ssh_target="tester@access-iris.uni.lu")
    queried = []

    def slurm(command, **kwargs):
        assert command[0] == "squeue"
        if "-j" in command:
            job = command[command.index("-j") + 1]
            queried.append(job)
            return subprocess.CompletedProcess(command, 0, "RUNNING\n" if job == "123" else "PENDING\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def execute(command, **kwargs):
        arguments = shlex.split(command[-1].split("; python3.11 -c ", 1)[1])
        monkeypatch.setattr(sys, "argv", ["status", *arguments[1:]])
        exec(compile(arguments[0], "<supervisor-status>", "exec"), {})
        return subprocess.CompletedProcess(command, 0, capsys.readouterr().out, "")

    original_scandir = os.scandir

    def bounded_scandir(path):
        if isinstance(path, (str, Path)) and Path(path).is_relative_to(tmp_path):
            assert "attempts" not in Path(path).relative_to(tmp_path).parts
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", bounded_scandir)
    monkeypatch.setattr(subprocess, "run", slurm)
    monkeypatch.setattr(resume_loop, "run_command", execute)
    result = resume_loop.remote_run_status(config)
    assert queried == ["123", "456"]
    assert result["worker_states"] == {"123": "RUNNING", "456": "PENDING"}
    assert result["active_workers"] == ["123", "456"]
    assert bool(result.get("optimization_completed")) is search_finished


def test_first_missing_run_skips_only_unneeded_staging_reclamation() -> None:
    should_skip = resume_loop._fresh_run_has_no_staging_to_reclaim
    assert should_skip({"state": "missing"}, {"submissions": 0})
    assert not should_skip({"state": "missing"}, {"submissions": 1})
    assert not should_skip(
        {"state": "missing"},
        {"submissions": 0, "last_submission_error": {"error": "staging failed"}},
    )
    assert not should_skip({"state": "resumable"}, {"submissions": 0})


def _ar_status_config(tmp_path, monkeypatch, run_dir, budget=9):
    config_file = tmp_path / "ar-status-config.yaml"
    config_file.write_text(f"max_experiments: {budget}\n")
    config = SimpleNamespace(remote_run_snapshot=str(run_dir), job_name="ar-run", autoresearch=True,
        runtime_config=config_file, ssh_port="8022", ssh_key="", ssh_target="tester@access-iris.uni.lu")
    fake_bin = tmp_path / "ar-status-bin"
    fake_bin.mkdir(exist_ok=True)
    for name in ("squeue", "sacct"):
        command = fake_bin / name
        command.write_text('#!/bin/sh\ncase "$*" in *-j*) printf "RUNNING\\n";; esac\n')
        command.chmod(0o755)
    def execute(command, **kwargs):
        arguments = shlex.split(command[-1].split("; python3.11 -c ", 1)[1])
        return subprocess.run([sys.executable, "-c", *arguments], text=True, capture_output=True,
            env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]})
    monkeypatch.setattr(resume_loop, "run_command", execute)
    return config


def _ar_checkpoint_status(tmp_path, monkeypatch, run_dir, budget=9):
    return resume_loop.remote_run_status(_ar_status_config(tmp_path, monkeypatch, run_dir, budget))


@pytest.mark.parametrize("completed", [0, 1, 9])
@pytest.mark.parametrize("counts", [(2, 1), (29.0, 6.0), (0, 0), (0.0, 0.0), (46, 15), (46.0, 15.0)])
def test_ar_status_counts_complete_evaluations_and_finds_nested_workers(tmp_path, monkeypatch, completed, counts):
    private = tmp_path / "private"
    private.mkdir()
    (private / "identity.json").write_text('{"sha256":"frozen"}')
    (private / "state.json").write_text(json.dumps({"rounds": [
        {"round": index, "guide_sha256": "fixed", "train": {"resolved": counts[0], "total": 46},
         "validation": {"resolved": counts[1], "total": 15}} for index in range(completed)],
        **({"pending": {"round": completed, "guide_sha256": "frozen-guide"}} if completed < 9 else {})}))
    (private / "research_exit.json").write_text(json.dumps(
        {"status": "completed", "completed_experiments": 9, "final": "Agent final account."}
        if completed == 9 else {"status": "agent_returned_or_interrupted"}))
    worker = private / "pce/round-0000/hpc_tasks/pce/fp/execution_windows/window_0000_0005/task_state.json"
    worker.parent.mkdir(parents=True)
    worker.write_text('{"phase":"SUBMITTED","active_job_id":"123"}')
    before = (private / "state.json").read_bytes()
    status = _ar_checkpoint_status(tmp_path, monkeypatch, tmp_path)
    assert status["completed_evaluations"] == completed
    assert status["evaluation_target"] == 9
    assert resume_loop.is_completed(status) is (completed == 9)
    assert status["active_workers"] == ["123"]
    assert status["runtime_initialized"]
    assert (private / "state.json").read_bytes() == before


@pytest.mark.parametrize("score", [
    {"resolved": value, "total": 46}
    for value in (0.5, -1, 47, True, False, "29", None, float("nan"), float("inf"), float("-inf"))
] + [{"total": 46}, {"resolved": 0, "total": 0}, {"resolved": 1, "total": 46.0},
     {"resolved": 0, "total": True}, {"resolved": 0}])
@pytest.mark.parametrize("split", ["train", "validation"])
def test_ar_status_rejects_invalid_counts_without_rewriting(tmp_path, monkeypatch, score, split):
    private = tmp_path / "private"
    private.mkdir()
    checkpoint = private / "state.json"
    row = {"round": 0, "guide_sha256": "fixed", "train": {"resolved": 29.0, "total": 46},
           "validation": {"resolved": 6.0, "total": 15}}
    row[split] = score
    checkpoint.write_text(json.dumps({"rounds": [row]}))
    before = checkpoint.read_bytes()
    status = _ar_checkpoint_status(tmp_path, monkeypatch, tmp_path)
    assert status["state"] == "status_check_failed"
    assert status["error_type"] == "invalid_ar_checkpoint"
    assert checkpoint.read_bytes() == before


@pytest.mark.parametrize("completed", [0, 1, 8, 9])
@pytest.mark.parametrize("owner_exit", ["running", "failed", "budget_exhausted", "completed"])
def test_ar_completion_requires_scores_and_agent_finalization(tmp_path, monkeypatch, completed, owner_exit):
    private = tmp_path / "private"
    private.mkdir()
    (private / "state.json").write_text(json.dumps({"rounds": [
        {"round": index, "guide_sha256": "fixed", "train": {"resolved": 0.0, "total": 46},
         "validation": {"resolved": 15.0, "total": 15}} for index in range(completed)]}))
    (private / "research_exit.json").write_text(json.dumps({"status": owner_exit, "error_type": "OSError",
        "completed_experiments": 9, "final": "Agent final account."}))
    status = _ar_checkpoint_status(tmp_path, monkeypatch, tmp_path)
    assert status["completed_evaluations"] == completed
    assert resume_loop.is_completed(status) is (completed == 9 and owner_exit == "completed")
    if completed == 9 and owner_exit == "completed":
        assert status["status"] == "completed"
    elif owner_exit == "failed":
        assert status["state"] == "controller_failed"
    else:
        assert status["state"] == "resumable"
    if completed == 9:
        assert status["evaluations_complete"] is True
        assert status["phase"] == "research_finalization"


@pytest.mark.parametrize("receipt", [
    {"status": "completed", "completed_experiments": 1},
    {"status": "completed", "completed_experiments": 0, "final": "Agent final account."},
    {"status": "completed", "completed_experiments": 1, "final": ""},
])
def test_ar_incomplete_final_record_keeps_completed_scores_resumable(tmp_path, monkeypatch, receipt):
    private = tmp_path / "private"
    private.mkdir()
    (private / "state.json").write_text(json.dumps({"rounds": [
        {"round": 0, "guide_sha256": "fixed", "train": {"resolved": 1.0, "total": 1},
         "validation": {"resolved": 0.0, "total": 1}}]}))
    (private / "research_exit.json").write_text(json.dumps(receipt))
    status = _ar_checkpoint_status(tmp_path, monkeypatch, tmp_path, budget=1)
    assert status["evaluations_complete"] is True
    assert status["state"] == "resumable" and not resume_loop.is_completed(status)


def test_ar_status_reads_real_pce_scores_and_pending_resume_reuses_results(tmp_path, monkeypatch):
    from src.optimization.autoresearch_pce import PCEExperiments
    from src.optimization.safe_pce_learning import SafePCEBackend
    from tests.test_optimization.test_codex_pce_research import _fixture, _executor

    config = _fixture(tmp_path)
    calls = _executor(monkeypatch, fail_once=True)
    experiments = PCEExperiments(config, SafePCEBackend)
    seed = experiments.evaluate("evaluate")
    assert type(seed["train"]["resolved"]) is float
    assert type(seed["validation"]["resolved"]) is float
    experiments.guide.write_text("Explore the task contracts.")
    with pytest.raises(OSError, match="synthetic evaluator interruption"):
        experiments.evaluate("evaluate")
    checkpoint = config.run_dir / "private/state.json"
    before = checkpoint.read_bytes()
    pending = json.loads(before)["pending"]
    status = _ar_checkpoint_status(tmp_path, monkeypatch, config.run_dir, config.max_experiments)
    assert status["state"] == "resumable"
    assert status["completed_evaluations"] == 1
    assert status["pending"] == pending
    assert checkpoint.read_bytes() == before
    assert len(calls) == 4

    resumed = PCEExperiments(config, SafePCEBackend)
    update = resumed.evaluate("resume")
    assert len(calls) == 5  # Only the unfinished validation case is executed again.
    assert resumed.state["rounds"][0] == json.loads(before)["rounds"][0]
    assert update["guide_sha256"] == pending["guide_sha256"]
    status = _ar_checkpoint_status(tmp_path, monkeypatch, config.run_dir, config.max_experiments)
    assert status["state"] == "resumable"
    assert status["completed_evaluations"] == 2


def test_ar_supervisor_reads_real_scores_waits_workers_and_resumes_once(tmp_path, monkeypatch):
    from dataclasses import replace
    from src.optimization.autoresearch_pce import PCEExperiments
    from src.optimization.safe_pce_learning import SafePCEBackend
    from tests.test_optimization.test_codex_pce_research import _fixture, _executor

    config = replace(_fixture(tmp_path), max_experiments=2)
    calls = _executor(monkeypatch, fail_once=True)
    experiments = PCEExperiments(config, SafePCEBackend)
    experiments.evaluate("evaluate")
    experiments.guide.write_text("Explore existing behavior.")
    with pytest.raises(OSError, match="synthetic evaluator interruption"):
        experiments.evaluate("evaluate")
    checkpoint = experiments.private / "state.json"
    before = checkpoint.read_bytes()
    pending = json.loads(before)["pending"]
    worker = experiments.private / "pce/round-0001/hpc_tasks/pce/fp/execution_windows/window_0000_0005/task_state.json"
    worker.parent.mkdir(parents=True)
    worker.write_text('{"phase":"SUBMITTED","active_job_id":"123"}')
    supervisor = _ar_status_config(tmp_path, monkeypatch, config.run_dir, budget=2)
    supervisor.submit, supervisor.once = True, True
    supervisor.slice_time, supervisor.poll_interval_seconds, supervisor.target_iterations = "00:30:00", 0, 0
    supervisor.state_file = tmp_path / "supervisor.json"
    state, submitted = {"submissions": 1}, []
    monkeypatch.setattr(resume_loop, "_load_supervisor_state", lambda _: state)
    monkeypatch.setattr(resume_loop, "_submission_identity_error", lambda _: None)
    monkeypatch.setattr(resume_loop, "reclaim_remote_staging", lambda _: {"removed": 0})
    monkeypatch.setattr(resume_loop, "reclaim_remote_workspaces", lambda *args, **kwargs: {"removed": 0})
    def submit(_):
        submitted.append("resumed-owner")
        return submitted[-1]
    monkeypatch.setattr(resume_loop, "submit_slice", submit)

    # The real status reader sees float scores and an active nested window.
    assert resume_loop.run_loop(supervisor) == 0
    assert state["status"] == "waiting_workers" and not submitted
    assert checkpoint.read_bytes() == before and len(calls) == 4
    worker.write_text('{"phase":"COMPLETE","active_job_id":"123"}')
    assert resume_loop.run_loop(supervisor) == 0
    assert state["status"] == "controller_submitted" and submitted == ["resumed-owner"]
    assert checkpoint.read_bytes() == before and len(calls) == 4

    # External model/Slurm work is injected; PCE caching and checkpoints are real.
    resumed = PCEExperiments(config, SafePCEBackend)
    result = resumed.evaluate("resume")
    assert result["guide_sha256"] == pending["guide_sha256"] and len(calls) == 5
    scored = resume_loop.remote_run_status(supervisor)
    assert scored["evaluations_complete"] and not resume_loop.is_completed(scored)
    # The already submitted Owner must finish its Agent turn as well.
    (experiments.private / "research_exit.json").write_text(json.dumps({"status": "completed",
        "completed_experiments": 2, "final": "Recorded results and restored the selected guide."}))
    assert resume_loop.run_loop(supervisor) == 0
    assert state["status"] == "completed" and submitted == ["resumed-owner"]
    assert state["last_remote_status"]["completed_evaluations"] == 2
    assert json.loads(supervisor.state_file.read_text())["status"] == "completed"
    assert resumed.evaluate("evaluate")["status"] == "budget_exhausted"
    assert len(calls) == 5


def test_ar_supervisor_uses_configured_evaluation_budget_not_iteration_option(tmp_path):
    config_file = tmp_path / "ar.yaml"
    config_file.write_text(f"mode: autoresearch_pce\nmax_experiments: 9\npaths:\n  run_dir: {REPO_ROOT / 'output/AR/test'}\n")
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text("host: example.invalid\nuser: tester\n")
    arguments = ["--autoresearch-config", str(config_file), "--ulhpc-config", str(ulhpc)]
    config = resume_loop.parse_args(arguments)
    assert config.autoresearch and not config.offline_gepa and config.target_iterations == 0
    assert config.remote_run_snapshot.endswith("/run_state/output/AR/test")
    with pytest.raises(SystemExit, match="max_experiments"):
        resume_loop.parse_args(["--target-iterations", "8", *arguments])


def test_remote_workspace_reclamation_is_opt_in_and_uses_exact_run_root(
    monkeypatch,
) -> None:
    disabled = SimpleNamespace(reclaim_workspaces=False)
    assert reclaim_remote_workspaces(disabled) == {"removed": 0, "disabled": True}

    observed: list[list[str]] = []

    def fake_run(command, *, check=False):
        observed.append(command)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout='{"removed": 3}\n',
            stderr="",
        )

    monkeypatch.setattr(resume_loop, "run_command", fake_run)
    run_root = "/scratch/users/tester/project/run_state/output/one-run"
    enabled = SimpleNamespace(
        reclaim_workspaces=True,
        remote_run_snapshot=run_root,
        ssh_port="8022",
        ssh_key="",
        ssh_target="tester@access-aion.uni.lu",
    )

    assert reclaim_remote_workspaces(enabled, dry_run=True) == {"removed": 3}
    assert len(observed) == 1
    assert observed[0][:4] == [
        "ssh",
        "-p",
        "8022",
        "tester@access-aion.uni.lu",
    ]
    remote_command = observed[0][-1]
    assert "VIBE_HPC_WORKSPACE_RECLAIM" in remote_command
    assert "reclaim_disposable_phase_workspaces" in remote_command
    assert run_root in remote_command
    assert "dry_run=True" in remote_command


def test_repo_relative_preserves_worktree_local_symlink(tmp_path: Path) -> None:
    linked_target = tmp_path / "shared-output"
    linked_target.mkdir()
    link = REPO_ROOT / ".tmp_hpc_smoke" / "linked-output"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(linked_target, target_is_directory=True)
    try:
        assert _repo_relative(link / "run", "run_dir") == (
            ".tmp_hpc_smoke/linked-output/run"
        )
    finally:
        link.unlink(missing_ok=True)


def _write_config(root: Path) -> Path:
    snapshot = root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    rules = root / "rules.md"
    rules.write_text("1. rule\n", encoding="utf-8")
    run_dir = root / "run"
    config = root / "gepa.yaml"
    config.write_text(
        f"""
paths:
  dataset_snapshot: {snapshot}
  initial_rules: {rules}
  run_dir: {run_dir}
checker:
  model: deepseek-v4-flash
reflection:
  model: deepseek-v4-flash
search:
  max_metric_calls: 1
container:
  runtime: apptainer
  sif_cache_dir: /scratch/test/sif-cache
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )
    return config


def _write_workflow_config(root: Path) -> Path:
    run_dir = root / "run"
    config = root / "workflow.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"mode: polybench_pcce\npaths:\n  run_dir: {run_dir}\n",
        encoding="utf-8",
    )
    return config


def test_evaluator_repair_supervisor_monitors_repair_root() -> None:
    config = (
        REPO_ROOT
        / "configs/archive/polybench_pcce/polybench_pcce_hpc_formal_seed.yaml"
    )
    snapshot = _remote_run_snapshot(["--resume-evaluator", "native-home"], config)
    assert snapshot.endswith(
        "/output/SWE-PolyBench/polybench-pcce-runs/formal/"
        "seed-python111-20260817/evaluator_repairs/native-home"
    )


def test_swe_verified_evaluator_repair_supervisor_monitors_repair_root() -> None:
    config = REPO_ROOT / "configs/swe_verified_safe_pce_audit10_v7_20260913.yaml"
    snapshot = _remote_run_snapshot(
        ["--evaluator-repair-id", "evalfix-v1"], config
    )
    assert snapshot.endswith(
        "/output/SWE-bench_Verified/swe-verified-pce-runs/development/"
        "safe-pce-audit10-v7-20260913/evaluator_repairs/evalfix-v1"
    )


def _fake_batch_script(path: Path, log_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> {log_path}
if [[ " $* " == *" --submit "* ]]; then
  count_file="{log_path}.count"
  count=0
  [[ -f "$count_file" ]] && count=$(cat "$count_file")
  count=$((count + 1))
  printf '%s' "$count" > "$count_file"
  printf '{{"job_id":"job-%s"}}\\n' "$count"
else
  printf '%s\\n' "$@"
fi
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fake_ssh(path: Path, status_sequence: Path, log_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
cmd="${{@: -1}}"
printf '%s\\n' "$cmd" >> {log_path}
if [[ "$cmd" == *"VIBE_HPC_RUN_STATUS"* ]]; then
  line=$(head -n 1 {status_sequence})
  tail -n +2 {status_sequence} > {status_sequence}.tmp
  mv {status_sequence}.tmp {status_sequence}
  printf '%s\\n' "$line"
elif [[ "$cmd" == *"VIBE_OFFLINE_TARGET_EXTENSION"* ]]; then
  printf '{{"from":8,"to":20,"additional_iterations":12}}\\n'
elif [[ "$cmd" == squeue* ]]; then
  exit 0
elif [[ "$cmd" == sacct* ]]; then
  printf 'COMPLETED|0:0\\n'
else
  exit 1
fi
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _env(fake_bin: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["ULHPC_USER"] = "tester"
    env["ULHPC_HOST"] = "example.invalid"
    env["ULHPC_PORT"] = "2222"
    return env


def _config_identity(config: Path) -> dict[str, str]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "runtime_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "repo_commit": commit,
    }


def test_ar_supervisor_resumes_after_early_exit_waits_workers_and_stops_at_nine(tmp_path):
    root = REPO_ROOT / ".tmp_hpc_smoke/ar_supervisor"
    root.mkdir(parents=True, exist_ok=True)
    config = root / "ar.yaml"
    config.write_text(f"mode: autoresearch_pce\nmax_experiments: 9\npaths:\n  run_dir: {root / 'run'}\n")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    status_file = tmp_path / "status.jsonl"
    status_file.write_text("\n".join(json.dumps(row) for row in [
        {"state": "resumable", "completed_evaluations": 0, "active_workers": ["old-worker"]},
        {"state": "resumable", "completed_evaluations": 0},
        {"state": "resumable", "completed_evaluations": 1},
        {"state": "result", "status": "completed", "completed_evaluations": 9}]) + "\n")
    ssh_log = tmp_path / "ssh.log"
    _fake_ssh(fake_bin / "ssh", status_file, ssh_log)
    batch = tmp_path / "batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(batch, batch_log)
    state_file = tmp_path / "state.json"
    command = [sys.executable, str(SCRIPT), "--autoresearch-config", str(config), "--submit",
        "--slice-time", "00:30:00", "--poll-interval", "0", "--job-name", "ar-supervisor",
        "--batch-script", str(batch), "--state-file", str(state_file)]
    result = subprocess.run(command, text=True, capture_output=True, cwd=REPO_ROOT, env=_env(fake_bin), timeout=30)
    assert result.returncode == 0, result.stderr
    state = json.loads(state_file.read_text())
    assert state["submissions"] == 2 and state["status"] == "completed"
    assert state["last_remote_status"]["completed_evaluations"] == 9
    assert "waiting without submission" in result.stdout
    assert batch_log.read_text().count("00:30:00") == 2
    before = ssh_log.read_bytes()
    # Restarting a completed local service must not revive an old/deleted run.
    again = subprocess.run(command, text=True, capture_output=True, cwd=REPO_ROOT, env=_env(fake_bin), timeout=30)
    assert again.returncode == 0 and ssh_log.read_bytes() == before


def test_hpc_resume_loop_dry_run_delegates_slice_time(tmp_path: Path) -> None:
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_dry"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:30:00",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--job-name",
            "dry-test",
            "--time",
            "24:00:00",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert "--time\n00:30:00" in batch_log.read_text(encoding="utf-8")
    assert "24:00:00" not in batch_log.read_text(encoding="utf-8")
    assert "--dry-run" in batch_log.read_text(encoding="utf-8")
    assert "--submit" not in batch_log.read_text(encoding="utf-8")


def test_supervisor_supplies_canonical_remote_paths() -> None:
    args = _with_default_remote_paths(
        ["--config", "configs/workflow.yaml"],
        batch_script=REPO_ROOT / "scripts/hpc_submit_swe_verified_pce.sh",
        remote_user="tester",
        job_name="safe-smoke-v3",
    )
    assert _take_arg(args, "--remote-dir") == (
        "/scratch/users/tester/vibe-coding-planning/staging/safe-smoke-v3"
    )
    assert _take_arg(args, "--remote-dataset-dir") == (
        "/scratch/users/tester/vibe-coding-planning/datasets"
    )
    assert _take_arg(args, "--remote-run-dir") == (
        "/scratch/users/tester/vibe-coding-planning/run_state"
    )


def test_supervisor_follows_thin_submit_wrapper_for_remote_path_support() -> None:
    args = _with_default_remote_paths(
        ["--config", "configs/workflow.yaml"],
        batch_script=REPO_ROOT
        / "scripts/hpc_submit_swe_verified_plan_ce_replay.sh",
        remote_user="tester",
        job_name="recovered-plan-ce",
    )

    assert _take_arg(args, "--remote-dir") == (
        "/scratch/users/tester/vibe-coding-planning/staging/recovered-plan-ce"
    )
    assert _take_arg(args, "--remote-dataset-dir") == (
        "/scratch/users/tester/vibe-coding-planning/datasets"
    )
    assert _take_arg(args, "--remote-run-dir") == (
        "/scratch/users/tester/vibe-coding-planning/run_state"
    )


def _take_arg(args: list[str], option: str) -> str:
    return args[args.index(option) + 1]


def test_explicit_remote_path_overrides_supervisor_default() -> None:
    args = _with_default_remote_paths(
        ["--remote-run-dir", "/scratch/frozen/run", "--config", "old.yaml"],
        batch_script=REPO_ROOT / "scripts/hpc_submit_swe_verified_pce.sh",
        remote_user="tester",
        job_name="historical-replay",
    )
    assert args.count("--remote-run-dir") == 1
    assert _take_arg(args, "--remote-run-dir") == "/scratch/frozen/run"


def test_hpc_resume_loop_accepts_non_gepa_workflow_config(tmp_path: Path) -> None:
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_workflow_resume_dry"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_workflow.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:10:00",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--config",
            str(config),
            "--job-name",
            "workflow-dry-test",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    batch_text = batch_log.read_text(encoding="utf-8")
    assert f"--config\n{config}" in batch_text
    assert "--time\n00:10:00" in batch_text
    assert "--dry-run" in batch_text


def test_new_run_skips_workspace_reclamation_until_manifest_exists(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_new_run_reclaim"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_workflow.sh"
    batch_log = tmp_path / "batch.log"
    ssh_log = tmp_path / "ssh.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, ssh_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--config",
            str(config),
            "--reclaim-staging",
            "--reclaim-workspaces",
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "--submit" in batch_log.read_text(encoding="utf-8")
    assert "VIBE_HPC_STAGING_RECLAIM" not in ssh_log.read_text(encoding="utf-8")
    assert "VIBE_HPC_WORKSPACE_RECLAIM" not in ssh_log.read_text(encoding="utf-8")


def test_hpc_resume_loop_treats_completed_with_incomplete_as_terminal(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_workflow_terminal"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_workflow.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":"completed_with_incomplete"}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--poll-interval",
            "0",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"


def test_hpc_resume_loop_resubmits_until_completed(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_submit"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    ssh_log = tmp_path / "ssh.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        "\n".join(
            [
                '{"state":"missing"}',
                '{"state":"resumable"}',
                '{"state":"result","status":"completed"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, ssh_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:10:00",
            "--check-interval",
            "0",
            "--poll-interval",
            "0",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--job-name",
            "resume-test",
            "--remote-run-dir",
            "~/remote-run",
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("[hpc-resume] submitted job_id=") == 2
    batch_text = batch_log.read_text(encoding="utf-8")
    assert batch_text.count("--submit") == 2
    assert batch_text.count("00:10:00") == 2
    assert "~/remote-run/.tmp_hpc_smoke/test_hpc_resume_loop_submit/run" in (
        ssh_log.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("failure", [
    {"error_type": "PermanentProviderError"},
    {"error_type": "IncompletePCEEvaluation", "failure_record": {"details": [
        {"failure_kind": "task_attempts_exhausted", "attempt": 3, "max_attempts": 3}]}},
    {"error_type": "TaskBatchBlocked", "failure_record": {
        "failure_kind": "task_attempts_exhausted", "attempt": 3, "max_attempts": 3}},
])
def test_hpc_resume_loop_stops_on_confirmed_exhaustion(tmp_path: Path, failure) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_failed"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(json.dumps({"state": "result", "status": "failed", **failure}) + "\n", encoding="utf-8")
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--check-interval",
            "0",
            "--poll-interval",
            "0",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 1
    assert "run is blocked; not resubmitting" in result.stderr
    assert not batch_log.exists()


def test_hpc_resume_loop_retries_controller_failure_without_named_exception(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_controller_recovery"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"controller_failed","controller_status":"failed",'
        '"error_type":"TaskBatchBlocked"}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_file = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--poll-interval",
            "0",
            "--state-file",
            str(state_file),
            "--batch-script",
            str(fake_batch),
            "--config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "retrying incomplete Controller work" in result.stdout
    assert batch_log.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["controller_failure_recoveries"] == 1
    assert state["status"] == "controller_submitted"


def test_hpc_supervisor_retries_status_check_failure_without_submitting(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_status_failure"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)
    (fake_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 255\n", encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0
    assert "retrying without submission" in result.stderr
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "waiting_after_status_check_failure"


def test_hpc_supervisor_retains_loop_after_submission_failure(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_submission_failure"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    fake_batch.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_batch.chmod(0o755)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0
    assert "retrying later" in result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "waiting_after_submission_failure"
    assert state["submissions"] == 0


def test_hpc_supervisor_stops_after_sync_disk_full(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_submission_disk_full"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    fake_batch.write_text(
        "#!/usr/bin/env bash\nprintf 'SYNC_DISK_FULL: quota exceeded\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    fake_batch.chmod(0o755)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 1
    assert "stopping without retry" in result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "blocked_submission_error"
    assert state["last_submission_error"]["marker"] == "SYNC_DISK_FULL"
    assert state["submissions"] == 0


def test_hpc_supervisor_retries_staging_error(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_staging_error"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    fake_batch.write_text(
        "#!/usr/bin/env bash\nprintf 'STAGING_ERROR: link collision\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    fake_batch.chmod(0o755)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "waiting_after_submission_failure"


def test_hpc_supervisor_blocks_when_runtime_config_changes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_config_identity"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    original = config.read_text(encoding="utf-8")
    process = subprocess.Popen(
        [
            "python",
            str(SCRIPT),
            "--poll-interval",
            "1",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(fake_bin),
    )
    for _ in range(100):
        if batch_log.exists():
            break
        time.sleep(0.02)
    config.write_text(original + "\n# changed\n", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 1, (stdout, stderr)
    assert batch_log.read_text(encoding="utf-8").count("--submit") == 1
    assert "runtime config changed after supervisor start" in stderr
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "blocked_identity_mismatch"


def test_hpc_supervisor_service_starts_with_tmux_and_caffeinate(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--session",
            "online-gepa-test",
            "--log",
            str(tmp_path / "supervisor.log"),
            "--poll-interval",
            "1800",
            "--gepa-config",
            "configs/archive/online_gepa/gepa_online_planning_pilot.yaml",
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "new-session -d -s online-gepa-test" in invocation
    assert (
        "exec caffeinate -i -s conda run --no-capture-output -n mini-swe python"
        in invocation
    )
    assert "hpc_resume_loop.py --poll-interval 1800" in invocation


def test_hpc_supervisor_service_uses_persisted_launch_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    launch_config = tmp_path / "launch.yaml"
    launch_config.write_text(
        """
schema_version: 1
session: persisted-online-gepa
log: .local/hpc-supervisor/persisted.log
arguments:
  - --target-iterations
  - "8"
  - --gepa-config
  - configs/archive/online_gepa/gepa_online_planning_hpc.yaml
  - --submit
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            str(launch_config),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "new-session -d -s persisted-online-gepa" in invocation
    assert "hpc_resume_loop.py --target-iterations 8" in invocation
    assert "configs/archive/online_gepa/gepa_online_planning_hpc.yaml --submit" in invocation
    assert "conda run --no-capture-output -n mini-swe" in invocation


def test_hpc_supervisor_service_starts_persisted_resume_sequence(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    launch_config = tmp_path / "sequence.yaml"
    launch_config.write_text(
        """
schema_version: 1
program: resume_sequence
session: paired-sequence
log: .local/hpc-supervisor/paired-sequence.log
runs:
  - name: first
    arguments: [--gepa-rules, --gepa-config, first.yaml]
  - name: second
    arguments: [--gepa-rules, --gepa-config, second.yaml]
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["python", str(SERVICE_SCRIPT), "start", "--launch-config", str(launch_config)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "new-session -d -s paired-sequence" in invocation
    assert "hpc_resume_sequence.py --launch-config" in invocation
    assert str(launch_config.resolve()) in invocation


def test_hpc_resume_sequence_dry_run_preserves_order(tmp_path: Path) -> None:
    launch_config = tmp_path / "sequence.yaml"
    launch_config.write_text(
        """
schema_version: 1
program: resume_sequence
runs:
  - name: replay
    arguments: [--gepa-rules, --gepa-config, replay.yaml]
  - name: manual
    arguments: [--gepa-rules, --gepa-config, manual.yaml]
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "python",
            str(SEQUENCE_SCRIPT),
            "--launch-config",
            str(launch_config),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.index("phase 1/2 start: replay") < result.stdout.index(
        "phase 2/2 start: manual"
    )
    assert result.stdout.index("replay.yaml") < result.stdout.index("manual.yaml")


def test_pcce_supervisor_launch_config_uses_shared_resume_loop(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/polybench_pcce/polybench_pcce_supervisor_smoke.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "hpc_resume_loop.py --poll-interval 600" in invocation
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--config configs/archive/polybench_pcce/polybench_pcce_hpc_smoke.yaml"
        in invocation
    )


def test_formal_pcce_supervisor_launch_config_uses_formal_seed_runtime(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/supervisor_launches/polybench_pcce_supervisor_formal_seed_clean_20260826.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "hpc_resume_loop.py --poll-interval 600" in invocation
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--config configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml"
        in invocation
    )
    assert "--require-clean-worktree" in invocation


def test_c4_checker_only_supervisor_uses_pcce_resume_loop(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/supervisor_launches/polybench_pc_checker_only_c4_balanced20_supervisor_v1_20260831.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "caffeinate -i -s" in invocation
    assert (
        "--state-file .local/hpc-supervisor/"
        "polybench-pc-c4-balanced20-v1-20260831.json" in invocation
    )
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--ulhpc-config /Users/taoran.wang/.config/ulhpc-submit/config.yaml"
        in invocation
    )
    assert (
        "--config configs/polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml"
        in invocation
    )
    assert "--require-clean-worktree" in invocation
    assert "--submit" in invocation


def test_c4_full_pcce_supervisor_is_bounded_and_uses_pcce_resume_loop(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/supervisor_launches/polybench_pcce_c4_balanced20_full_supervisor_v1_20260903.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "caffeinate -i -s" in invocation
    assert "hpc_resume_loop.py --poll-interval 300" in invocation
    assert "--slice-time 00:10:00" in invocation
    assert "--max-runs 24" in invocation
    assert (
        "--state-file .local/hpc-supervisor/"
        "polybench-pcce-c4-balanced20-v1-20260903.json" in invocation
    )
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--config configs/polybench_pcce_c4_balanced20_full_v1_20260903.yaml"
        in invocation
    )
    assert "--job-name polybench-pcce-c4-balanced20-v1-20260903" in invocation
    assert "--require-clean-worktree" in invocation
    assert "--submit" in invocation


def test_c5_repair3_supervisor_is_bounded_and_uses_pcce_resume_loop(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/supervisor_launches/polybench_pcce_c5_repair3_supervisor_v1_20260908.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "caffeinate -i -s" in invocation
    assert "hpc_resume_loop.py --poll-interval 300" in invocation
    assert "--slice-time 00:10:00" in invocation
    assert "--max-runs 24" in invocation
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert "--config configs/polybench_pcce_c5_repair3_v1_20260908.yaml" in invocation
    assert "--job-name polybench-pcce-c5-repair3-v1-20260908" in invocation
    assert "--require-clean-worktree" in invocation
    assert "--submit" in invocation


def test_clean_seed_pcce_dependency_repair_supervisor_selects_frozen_subset(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
                "configs/archive/supervisor_launches/"
                "polybench_pcce_supervisor_formal_seed_dependency_cache_clean_20260826.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "--resume-evaluator clean-depcache-v1-20260826" in invocation
    assert (
        "--config configs/"
        "polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml"
        in invocation
    )
    assert (
        "--resume-evaluator-instances-file configs/frozen_dependency_caches/"
        "polybench_evaluator_dependencies_formal_v2_20260823/"
        "evaluator_repair_subset_clean99.json" in invocation
    )
    assert "--require-clean-worktree" in invocation


def test_formal_pcce_contract_retry_uses_new_supervisor_state_only(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            "configs/archive/polybench_pcce/"
            "polybench_pcce_supervisor_formal_seed_contract_retry_20260818.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "--config configs/polybench_pcce_hpc_formal_seed.yaml" in invocation
    assert "--job-name polybench-pcce-seed-formal-20260817" in invocation
    assert (
        "--state-file .local/hpc-supervisor/"
        "polybench-pcce-seed-formal-contract-retry-submit-20260818.json" in invocation
    )
    assert "--recover-controller-error-type-once TaskBatchBlocked" in invocation
    assert "--require-clean-worktree" in invocation


def test_hpc_supervisor_waits_for_workers_without_submitting(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_wait"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"resumable","completed_iterations":2,'
        '"first_observed_completed_iterations":2,'
        '"active_controllers":[],"active_workers":["9001"]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "3",
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "waiting without submission" in result.stdout
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["target_iterations"] == 3
    assert state["last_completed_iterations"] == 2


def test_hpc_supervisor_stops_at_cumulative_iteration_target(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_target"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"resumable","completed_iterations":5,'
        '"first_observed_completed_iterations":2,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "submissions": 1,
                "remote_run_snapshot": (
                    "~/hpc_run_state/vibe-coding-planning/"
                    ".tmp_hpc_smoke/test_hpc_supervisor_target/run"
                ),
                "job_name": "vibe-gepa",
                "target_additional_iterations": 3,
                "baseline_iterations": 2,
                "target_completed_iterations": 5,
                **_config_identity(config),
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "5",
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "iteration target reached" in result.stdout
    assert not batch_log.exists()
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["target_iterations"] == 5


@pytest.mark.parametrize("search_finished", [False, True])
def test_gepa_target_without_result_resubmits_unfinished_finalization(monkeypatch, search_finished):
    state = {"submissions": 1}
    config = SimpleNamespace(submit=True, slice_time="00:08:00", poll_interval_seconds=0,
        target_iterations=20, remote_run_snapshot="synthetic-private-run", once=True,
        offline_gepa=True)
    monkeypatch.setattr(resume_loop, "_supervisor_lock", lambda config: nullcontext())
    monkeypatch.setattr(resume_loop, "_load_supervisor_state", lambda config: state)
    monkeypatch.setattr(resume_loop, "_save_supervisor_state", lambda *a: None)
    monkeypatch.setattr(resume_loop, "_submission_identity_error", lambda config: None)
    monkeypatch.setattr(resume_loop, "remote_run_status", lambda config: {
        "state": "resumable", "completed_iterations": 20, "optimization_completed": search_finished,
        "active_controllers": [], "active_workers": []})
    monkeypatch.setattr(resume_loop, "reclaim_remote_staging", lambda *a: {"removed": 0})
    monkeypatch.setattr(resume_loop, "reclaim_remote_workspaces", lambda *a, **k: {"removed": 0})
    submitted = []
    monkeypatch.setattr(resume_loop, "submit_slice", lambda config: submitted.append(config) or "finalizer")
    assert resume_loop.run_loop(config) == 0
    assert len(submitted) == int(search_finished)
    assert state["status"] == ("controller_submitted" if search_finished else "iteration_target_reached")
    assert state["last_completed_iterations"] == 20


def test_hpc_supervisor_rejects_completion_before_iteration_target(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_short"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":"completed","completed_iterations":3,'
        '"first_observed_completed_iterations":0,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "8",
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 2
    assert "completed before cumulative iteration target" in result.stderr
    assert not batch_log.exists()


def test_hpc_supervisor_accepts_offline_controller_completion_status(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_offline_completion"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":null,"controller_status":"completed",'
        '"completed_iterations":2,"first_observed_completed_iterations":0,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "submissions": 1,
                "remote_run_snapshot": (
                    "~/hpc_run_state/vibe-coding-planning/"
                    ".tmp_hpc_smoke/test_offline_completion/run"
                ),
                "job_name": "vibe-gepa",
                "target_iterations": 2,
                **_config_identity(config),
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "2",
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert not batch_log.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_completed_iterations"] == 2
    assert state["status"] == "completed"


def test_offline_supervisor_natively_extends_completed_target_and_resumes(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_native_offline_extension"
    config = _write_config(local_root)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  max_metric_calls: 1\n",
            "  max_metric_calls: 1\n  max_iterations: 20\n",
        ),
        encoding="utf-8",
    )
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    ssh_log = tmp_path / "ssh.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        "\n".join(
            [
                '{"state":"result","status":"completed",'
                '"completed_iterations":8,"first_observed_completed_iterations":0,'
                '"active_controllers":[],"active_workers":[]}',
                '{"state":"resumable","completed_iterations":8,'
                '"first_observed_completed_iterations":0,'
                '"active_controllers":[],"active_workers":[]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, ssh_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "extended completed Offline target from=8 to=20" in result.stdout
    assert "VIBE_OFFLINE_TARGET_EXTENSION" in ssh_log.read_text(encoding="utf-8")
    assert batch_log.read_text(encoding="utf-8").count("--submit") == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["target_iterations"] == 20
    assert state["last_iteration_target_extension"]["from"] == 8
