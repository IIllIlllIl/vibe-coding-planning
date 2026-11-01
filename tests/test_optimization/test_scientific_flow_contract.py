"""Necessary-flow whitelist: diagnostics cannot change experiment results.

Only external model/container/scheduler boundaries are mocked by the existing
end-to-end fixture. The actual PCE Workers, checkpoints, ACE and GEPA execute.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization.audit import JsonlLogger, optional_diagnostic
from src.optimization.callbacks import ProgressCallback
from tests.test_optimization.test_safe_pce_smoke_dataflow import (
    test_verified_end_to_end_and_audit_detects_lost_evidence as exercise_flow,
)


def fail(*args, **kwargs):
    raise OSError("synthetic diagnostic storage failure")


@pytest.mark.parametrize("fault", [
    "events", "proposal_trace", "reports", "source_summary", "raw_patch_sidecar",
    "source_initialization",
])
def test_optional_failures_preserve_complete_scientific_flow(tmp_path, monkeypatch, fault):
    from src.optimization import behavioral_hybrid_ace, safe_pce_learning
    from src.swe_verified_pce import runner, hpc_executor
    if fault == "events":
        monkeypatch.setattr(JsonlLogger, "write", fail)
    elif fault == "proposal_trace":
        monkeypatch.setattr(behavioral_hybrid_ace, "atomic_json", fail)
    elif fault == "reports":
        original = Path.write_text
        def write(path, *args, **kwargs):
            if path.name in {"best_guideline.txt", "candidate_tree.html"}:
                fail()
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", write)
        atomic = safe_pce_learning.atomic_json
        def export(path, value):
            if path.name == "safe_pce_candidate_metrics.json":
                fail()
            return atomic(path, value)
        monkeypatch.setattr(safe_pce_learning, "atomic_json", export)
    elif fault == "raw_patch_sidecar":
        write = Path.write_text
        def record(path, *args, **kwargs):
            if path.name == "raw_code_submission.patch":
                fail()
            return write(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", record)
    elif fault == "source_summary":
        for module in (runner, hpc_executor):
            original = module.file_sha256
            def digest(path, original=original):
                if path.name == "source_access.jsonl":
                    fail()
                return original(path)
            monkeypatch.setattr(module, "file_sha256", digest)
    else:
        touch = Path.touch
        def initialize(path, *args, **kwargs):
            if path.name == "source_access.jsonl":
                fail()
            return touch(path, *args, **kwargs)
        monkeypatch.setattr(Path, "touch", initialize)

    exercise_flow(tmp_path, monkeypatch, "swe_rpg_pce", False, False, False)
    root = tmp_path / "run"
    result = json.loads((root / "result.json").read_text())
    assert len(result["candidates"]) == 2
    assert result["val_aggregate_scores"] == [0.0, 1.0]
    assert json.loads((root / "controller_status.json").read_text())["status"] == "completed"
    assert (root / "gepa_resume_state.json").is_file()
    assert len(json.loads((root / "global_counter_ledger.json").read_text())["history"]) == 2
    assert len(list((root / "hpc_tasks/pce").glob("*/checkpoints/task_*/plan.json"))) == 6
    assert not list(root.rglob("usage.jsonl"))
    assert not list(root.rglob("patch_submission.json"))
    assert not list(root.rglob("failure.json"))
    assert not list(root.rglob("raw_code_submission.patch"))
    assert not (root / "safe_pce_physical_evaluations.jsonl").exists()


def test_optional_logger_constructs_without_writable_parent(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("unchanged")
    logger = JsonlLogger(blocked / "events.jsonl")
    logger.write_optional("ignored")
    with pytest.raises(OSError):
        logger.write("required")
    assert blocked.read_text() == "unchanged"


def test_diagnostic_boundary_preserves_scheduler_control_flow():
    with pytest.raises(ControllerYield):
        with optional_diagnostic("test"):
            raise ControllerYield(batch_dir="batch", job_id="1", reason="waiting")


def test_checkpoint_failure_is_not_swallowed_by_gepa_callbacks(tmp_path):
    from gepa.core.callbacks import notify_callbacks
    checkpoint = SimpleNamespace(save=fail)
    callback = ProgressCallback(tmp_path, checkpoint=checkpoint)
    with pytest.raises(OfflineReflectionBlocked) as caught:
        notify_callbacks([callback], "on_state_saved",
                         {"iteration": 1, "run_dir": str(tmp_path)})
    assert isinstance(caught.value.cause, OSError)
    assert json.loads((tmp_path / "iteration_progress.json").read_text())["completed_iterations"] == 0


def test_agent_error_and_raw_response_reach_retry_without_sidecar(tmp_path):
    from src.optimization.hpc.worker_contract import worker_failure, retry_feedback
    exc = ValueError("submit a JSON object")
    exc.raw_response = "original invalid response"
    exc.trajectory = [{"role": "assistant", "content": exc.raw_response}]
    output = worker_failure({}, "start", "agent_execution", exc, contract_error=True)
    assert output["status"] == "agent_failed"
    assert output["failure_trajectory"] == exc.trajectory
    path = tmp_path / "previous.json"
    path.write_text(json.dumps(output))
    feedback = retry_feedback(path)
    assert str(exc) in feedback and exc.raw_response in feedback


def test_repository_pce_incomplete_output_retries_in_shared_batch(tmp_path, monkeypatch):
    from src.optimization.hpc.config import HPCConfig
    from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskFiles, atomic_json
    from src.swe_verified_pce import hpc_executor
    task = TaskFiles(0, "case", tmp_path / "tasks/task_0000.json",
                     tmp_path / "outputs/task_0000.json", tmp_path / "attempts/task_0000")
    atomic_json(task.manifest_path, {"instance_id": "case", "pce_run_index": 1})
    incomplete = {"status": "completed", "fingerprint": "frozen", "task_index": 0,
                  "instance_id": "case", "pce_run_index": 1}
    atomic_json(task.output_path, incomplete)
    original = task.output_path.read_bytes()
    atomic_json(tmp_path / "task_state.json", {"schema_version": 1, "fingerprint": "frozen",
                "phase": "PREPARED", "active_attempt": 1, "active_job_id": None})
    config = SimpleNamespace(run_dir=tmp_path, workspace_preparation="inline", pce_window_size=0,
                             hpc=HPCConfig(submit=True, max_task_attempts=3))
    executor = hpc_executor.SWEVerifiedPCEHPCExecutor(config)
    submissions = []
    executor.runtime = SlurmTaskBatch(config.hpc, submitter=lambda path: submissions.append(path) or "124")
    monkeypatch.setattr(hpc_executor, "build_array_script", lambda **kwargs: "#!/bin/sh\n")
    with pytest.raises(ControllerYield):
        executor._evaluate_wave(tmp_path, "frozen", [task])
    archived = tmp_path / "failed_outputs/attempt_01/task_0000.json"
    assert archived.read_bytes() == original and len(submissions) == 1
    atomic_json(task.output_path, dict(incomplete, pce_status="completed"))
    assert executor._evaluate_wave(tmp_path, "frozen", [task])[0]["pce_status"] == "completed"
    assert archived.read_bytes() == original


def test_workspace_handoff_with_unavailable_diagnostics(tmp_path, monkeypatch):
    from tests.test_pce_workspace_preparation import (
        test_window8_bounds_live_trees_and_preserves_full_batch_identity as exercise_handoff,
    )
    monkeypatch.setattr(JsonlLogger, "write", fail)
    exercise_handoff(tmp_path, monkeypatch, True, "swe_rpg_pce")
    assert not list(tmp_path.rglob("workspace_claims"))
    assert not list(tmp_path.rglob("preparation_result.json"))
    assert not list(tmp_path.rglob("slurm_logs"))
    for script in tmp_path.rglob("*.sbatch"):
        assert "#SBATCH --error=" not in script.read_text()


@pytest.mark.parametrize("valid_base", [True, False])
def test_repository_validation_independent_of_diagnostic_io(tmp_path, monkeypatch, valid_base):
    from tests.test_environment.test_repository_baseline import FakeEnvironment
    from src.environment.repository_baseline import restore_repository_to_base
    from src.exceptions import FatalError
    original = Path.write_text
    def write(path, *args, **kwargs):
        if path.name == "repository_baseline.json.tmp":
            fail()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", write)
    environment = FakeEnvironment(base_present=valid_base)
    if valid_base:
        evidence = restore_repository_to_base(environment, "abc", phase="plan", evidence_dir=tmp_path)
        assert evidence["after"]["head"]["output"] == "abc\n"
    else:
        with pytest.raises(FatalError, match="does not contain"):
            restore_repository_to_base(environment, "abc", phase="plan", evidence_dir=tmp_path)


@pytest.mark.parametrize("phase", ["plan", "code"])
def test_terminal_agent_failure_reuses_online_scoring_and_full_last_evidence(tmp_path, monkeypatch, phase):
    from src.swe_verified_pce import hpc_executor as transport
    from src.optimization.hpc.task_batch import TaskFiles, atomic_json
    from src.optimization.safe_pce_learning import outcome_score, _validate_learning_evidence
    task = TaskFiles(0, "case", tmp_path / "tasks/task_0000.json",
                     tmp_path / "outputs/task_0000.json", tmp_path / "attempts/task_0000")
    manifest = dict(fingerprint="fp", task_index=0, instance_id="case", case={})
    atomic_json(task.manifest_path, manifest)
    executor = transport.SWEVerifiedPCEHPCExecutor(SimpleNamespace(
        hpc=SimpleNamespace(), plan=SimpleNamespace(max_attempts=3), code=SimpleNamespace(max_attempts=3)))
    executor.case_from_dict = lambda _: object()
    monkeypatch.setattr(transport, "checkpoint_identity", lambda *a, **k: "checkpoint")
    raw = "original rejected submission " + "{{data}}\\n" * 1000
    base = dict(manifest, status="retryable_failed", failure_category="agent",
                terminal_phase=phase, terminal_reason="invalid_submission",
                failure_trajectory=[{"role": "assistant", "content": raw}])
    # A scheduler/environment attempt does not count toward Agent exhaustion.
    atomic_json(tmp_path / "failed_outputs/attempt_01/task_0000.json",
                dict(base, attempt=1, failure_category="io"))
    for attempt in (2, 3):
        atomic_json(tmp_path / f"failed_outputs/attempt_{attempt:02d}/task_0000.json",
                    dict(base, attempt=attempt))
    if phase == "code":
        atomic_json(tmp_path / "checkpoints/task_0000/plan.json", dict(
            checkpoint_identity="checkpoint", phase="plan",
            payload={"plan": "Exact completed Plan", "trajectory": [{"role": "assistant", "content": "Plan trace"}]}))
    final = dict(base, attempt=4)
    outcome = executor._finalize_agent_failure(task, final, batch_dir=tmp_path,
                                              state_dir=tmp_path, fingerprint="fp")
    assert outcome["agent_failure_attempts"] == [2, 3, 4]
    assert outcome[phase + "_trajectory"][0]["content"] == raw
    assert outcome["last_worker_output"] == final
    assert outcome["plan"] == ("Exact completed Plan" if phase == "code" else "")
    assert outcome["patch"] == ""
    _validate_learning_evidence(outcome)
    assert outcome_score(outcome, mode="swe_verified_pce", instance_id="case") == 0
    assert json.loads(task.output_path.read_text()) == outcome
    from src.optimization.pce_reflector_evidence import pce_reflector_views
    files, index = pce_reflector_views({"pce_runs": [{"pce_output": outcome}]}, "original.json")
    assert files[f"record/pce/run-01/{phase}_behavior.json"][0]["content"] == raw
    assert index["runs"]
    assert executor._finalize_agent_failure(task, dict(final, failure_category="evaluator"),
        batch_dir=tmp_path, state_dir=tmp_path, fingerprint="fp") is None


def test_atomic_storage_retry_does_not_reinvoke_agent_or_change_payload(tmp_path, monkeypatch):
    from src.optimization.hpc import task_batch
    original = Path.replace
    calls = []
    def replace(path, target):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("temporary filesystem failure")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(task_batch.time, "sleep", lambda _: None)
    payload = {"plan": "unaltered {{x}}", "trajectory": [{"content": "raw"}]}
    task_batch.atomic_json(tmp_path / "plan.json", payload)
    assert len(calls) == 2
    assert json.loads((tmp_path / "plan.json").read_text()) == payload


@pytest.mark.parametrize("record,stop", [
    ({"error_type": "PermanentProviderError"}, True),
    ({"errno": 28}, True),
    ({"slurm_state": "CANCELLED"}, True),
    ({"error_type": "TimeoutError"}, False),
    ({"error_type": "CodexConfigurationError", "retry_disposition": "block_run"}, False),
    ({"error_type": "ValueError", "error": "example mentions quota"}, False),
])
def test_stop_policy_requires_specific_evidence(record, stop):
    from src.exceptions import confirmed_run_stop
    assert confirmed_run_stop(record) is stop


def test_unknown_evaluation_is_retried_not_reused_as_checkpoint(tmp_path):
    from src.swe_verified_pce.runner import SWEVerifiedPCERunner
    from src.swe_verified_pce.evaluator import SWEVerifiedEvaluatorOperationalError
    from src.optimization.hpc.task_batch import atomic_json
    runner = object.__new__(SWEVerifiedPCERunner)
    runner.checkpoint_dir = tmp_path
    runner.checkpoint_identity = "same"
    payload = {"evaluator_result": {"task_outcome": "unknown", "outcome_reason": "parser_failed"}}
    atomic_json(tmp_path / "evaluate.json", {"checkpoint_identity": "same", "phase": "evaluate", "payload": payload})
    original = (tmp_path / "evaluate.json").read_bytes()
    assert runner._checkpoint("evaluate") is None
    with pytest.raises(SWEVerifiedEvaluatorOperationalError) as caught:
        runner._save_checkpoint("evaluate", payload)
    assert caught.value.evidence == payload["evaluator_result"]
    assert (tmp_path / "evaluate.json").read_bytes() == original
