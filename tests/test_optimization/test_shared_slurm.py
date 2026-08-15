"""No-network checks for the method-independent Slurm task contract."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.config import DockerConfig
from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization.config import (
    ContainerConfig,
    ModelConfig,
    OfflineSearchConfig,
    OfflineExecutionConfig,
    OptimizationConfig,
)
from src.optimization.checker import (
    CheckerAgentTimeout,
    CheckerOutputContractError,
    checker_retry_feedback,
)
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.slurm import SlurmTaskStatus
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskAttemptsExhausted,
    TaskBatchBlocked,
    TaskFiles,
    atomic_json,
)
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerTimeoutOutput,
    GEPACase,
    RepositoryEvidence,
    RepositoryRef,
)
from src.optimization.offline_checker_worker import run_task as run_checker_task
from src.optimization.offline_hpc_executor import (
    HPCSlurmOfflineCheckerExecutor,
    build_offline_checker_array_script,
    offline_evaluation_fingerprint,
)
from src.optimization.offline_hpc_reflection import (
    HPCOfflineReflectionProposer,
)
from src.optimization.offline_reflection_worker import (
    run_task as run_reflection_task,
)
from src.optimization.runner import run_optimization


def _case(*, resolved: bool, asi: dict | None = None) -> GEPACase:
    return GEPACase(
        instance_id="org__repo-1",
        split="train",
        resolved=resolved,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__repo-1"),
        asi=asi or {},
    )


def _config(tmp_path: Path) -> OptimizationConfig:
    model = ModelConfig(
        model="test",
        api_base="http://example.invalid",
        api_key_env="TEST_KEY",
        temperature=0.0,
        max_steps=5,
        cost_limit=1.0,
        timeout=30,
    )
    return OptimizationConfig(
        dataset_snapshot=tmp_path / "snapshot",
        initial_rules_path=tmp_path / "rules.md",
        run_dir=tmp_path / "run",
        checker=model,
        reflection=replace(model, temperature=0.7),
        search=OfflineSearchConfig(10, 10, 2, 42, 1),
        docker=DockerConfig(),
        container=ContainerConfig(runtime="apptainer"),
        checker_prompt="checker",
        checker_instance_template="{{task}}",
        reflection_prompt="reflection",
        reflection_instance_template="{{current_rules}}",
    )


def test_shared_slurm_batch_reuses_only_atomic_completed_output(tmp_path):
    task = TaskFiles(
        index=0,
        instance_id="case",
        manifest_path=tmp_path / "input.json",
        output_path=tmp_path / "output.json",
        attempts_dir=tmp_path / "attempts",
    )
    atomic_json(task.manifest_path, {"case": "case"})
    submissions: list[Path] = []

    def submit(script: Path) -> str:
        submissions.append(script)
        return "123"

    runtime = SlurmTaskBatch(HPCConfig(submit=True), submitter=submit)

    def write_script(indices, attempt):
        assert list(indices) == [0]
        path = tmp_path / f"attempt_{attempt}.sbatch"
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        return path

    def validate(files, output):
        assert files.instance_id == output["instance_id"]

    with pytest.raises(ControllerYield):
        runtime.run(
            batch_dir=tmp_path,
            fingerprint="fingerprint",
            tasks=[task],
            job_name=lambda attempt: f"job-{attempt}",
            write_script=write_script,
            validate_output=validate,
        )
    atomic_json(
        task.output_path,
        {"status": "completed", "instance_id": "case", "value": 7},
    )
    outputs = runtime.run(
        batch_dir=tmp_path,
        fingerprint="fingerprint",
        tasks=[task],
        job_name=lambda attempt: f"job-{attempt}",
        write_script=write_script,
        validate_output=validate,
    )

    assert outputs[0]["value"] == 7
    assert len(submissions) == 1
    assert json.loads((tmp_path / "task_state.json").read_text())["phase"] == "COMPLETE"


def test_controller_yield_is_not_an_agent_or_gepa_error():
    assert issubclass(ControllerYield, BaseException)
    assert not issubclass(ControllerYield, Exception)


def test_offline_reflection_block_bypasses_gepa_exception_boundary():
    assert issubclass(OfflineReflectionBlocked, BaseException)
    assert not issubclass(OfflineReflectionBlocked, Exception)


def test_shared_slurm_blocks_and_records_host_validation_failure(tmp_path):
    task = TaskFiles(
        index=0,
        instance_id="case",
        manifest_path=tmp_path / "input.json",
        output_path=tmp_path / "output.json",
        attempts_dir=tmp_path / "attempts" / "task_0000",
    )
    atomic_json(task.manifest_path, {"case": "case"})
    atomic_json(task.output_path, {"status": "completed", "fingerprint": "wrong"})
    runtime = SlurmTaskBatch(HPCConfig(submit=True))

    with pytest.raises(TaskBatchBlocked, match="blocking Slurm Agent"):
        runtime.run(
            batch_dir=tmp_path,
            fingerprint="fingerprint",
            tasks=[task],
            job_name=lambda attempt: f"job-{attempt}",
            write_script=lambda indices, attempt: tmp_path / "unused.sbatch",
            validate_output=lambda files, output: (_ for _ in ()).throw(
                ValueError("output fingerprint mismatch")
            ),
        )

    state = json.loads((tmp_path / "task_state.json").read_text())
    assert state["phase"] == "BLOCKED"
    assert state["active_job_id"] is None
    failure = json.loads(
        (task.attempts_dir / "attempt_01" / "host_validation_failure.json").read_text()
    )
    assert failure["failure_stage"] == "host_output_validation"
    assert failure["error"] == "output fingerprint mismatch"
    assert failure["instance_id"] == "case"


def test_shared_slurm_records_terminal_status_and_exhaustion(
    tmp_path,
    monkeypatch,
):
    task = TaskFiles(
        index=0,
        instance_id="case",
        manifest_path=tmp_path / "input.json",
        output_path=tmp_path / "output.json",
        attempts_dir=tmp_path / "attempts" / "task_0000",
    )
    atomic_json(task.manifest_path, {"case": "case"})
    atomic_json(
        tmp_path / "task_state.json",
        {
            "schema_version": 1,
            "fingerprint": "fingerprint",
            "phase": "SUBMITTED",
            "active_attempt": 1,
            "active_job_id": "123",
            "missing_since": {},
            "terminal_since": {},
        },
    )
    monkeypatch.setattr(
        "src.optimization.hpc.task_batch.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus(
            state="TIMEOUT",
            elapsed_seconds=300,
            raw="123_0|TIMEOUT|00:05:00",
        ),
    )
    runtime = SlurmTaskBatch(
        HPCConfig(
            submit=True,
            max_task_attempts=1,
            task_output_grace_seconds=0,
        )
    )

    with pytest.raises(TaskAttemptsExhausted, match="1 attempt"):
        runtime.run(
            batch_dir=tmp_path,
            fingerprint="fingerprint",
            tasks=[task],
            job_name=lambda attempt: f"job-{attempt}",
            write_script=lambda indices, attempt: tmp_path / "unused.sbatch",
            validate_output=lambda files, output: None,
        )

    state = json.loads((tmp_path / "task_state.json").read_text())
    assert state["phase"] == "EXHAUSTED"
    assert state["active_job_id"] is None
    assert state["last_job_id"] == "123"
    assert state["terminal_failure"]["failure_kind"] == "task_attempts_exhausted"
    slurm_status = json.loads(
        (task.attempts_dir / "attempt_01" / "slurm_status.json").read_text()
    )
    assert slurm_status["state"] == "TIMEOUT"
    assert slurm_status["raw"] == "123_0|TIMEOUT|00:05:00"

    with pytest.raises(TaskAttemptsExhausted, match="1 attempt"):
        runtime.run(
            batch_dir=tmp_path,
            fingerprint="fingerprint",
            tasks=[task],
            job_name=lambda attempt: f"job-{attempt}",
            write_script=lambda indices, attempt: tmp_path / "unused.sbatch",
            validate_output=lambda files, output: None,
        )


def test_hpc_reflection_exhaustion_remains_an_ordinary_proposal_failure(tmp_path):
    proposer = HPCOfflineReflectionProposer(_config(tmp_path))

    class ExhaustedRuntime:
        def run(self, **kwargs):
            raise TaskAttemptsExhausted(
                "Slurm Agent tasks failed after 3 attempts: reflection"
            )

    proposer.runtime = ExhaustedRuntime()
    with pytest.raises(TaskAttemptsExhausted, match="3 attempts"):
        proposer(
            {"rules": "parent rules"},
            {"rules": []},
            ["rules"],
        )

    assert proposer.failures[-1]["error_type"] == "TaskAttemptsExhausted"
    assert (
        proposer.failures[-1]["outcome"]
        == "proposal_failed_retry_new_minibatch"
    )
    assert (
        '"event": "reflection_failed"'
        in (proposer.config.run_dir / "audit_events.jsonl").read_text()
    )


def test_offline_checker_task_boundary_excludes_labels_and_asi(tmp_path):
    batch_dir = tmp_path / "batch"
    case = _case(
        resolved=True,
        asi={
            "generated_patch": "secret patch",
            "evaluator_result": {"resolved": True},
        },
    )
    tasks = HPCSlurmOfflineCheckerExecutor._prepare(
        batch_dir,
        fingerprint="fingerprint",
        batch=[case],
        rules="rules",
        capture_traces=True,
    )
    manifest = json.loads(tasks[0].manifest_path.read_text(encoding="utf-8"))

    assert manifest["checker_payload"] == case.checker_payload()
    serialized = json.dumps(manifest)
    assert '"resolved"' not in serialized
    assert "generated_patch" not in serialized
    assert "evaluator_result" not in serialized


def test_offline_checker_fingerprint_ignores_host_only_evidence(tmp_path):
    config = _config(tmp_path)
    first = _case(resolved=True, asi={"generated_patch": "one"})
    second = _case(resolved=False, asi={"generated_patch": "two"})

    assert offline_evaluation_fingerprint(
        config,
        batch=[first],
        rules="rules",
        capture_traces=True,
    ) == offline_evaluation_fingerprint(
        config,
        batch=[second],
        rules="rules",
        capture_traces=True,
    )


def test_offline_checker_fingerprint_separates_diagnostic_repetitions(tmp_path):
    config = _config(tmp_path)
    case = _case(resolved=True)

    first = offline_evaluation_fingerprint(
        config,
        batch=[case],
        rules="rules",
        capture_traces=True,
        evaluation_tag="repeat_01",
    )
    second = offline_evaluation_fingerprint(
        config,
        batch=[case],
        rules="rules",
        capture_traces=True,
        evaluation_tag="repeat_02",
    )

    assert first != second


def test_offline_checker_repetition_identity_is_fingerprinted_and_manifested(
    tmp_path,
):
    config = _config(tmp_path)
    base = _case(resolved=True)
    first_case = replace(base, repetition_index=0)
    second_case = replace(base, repetition_index=1)

    first = offline_evaluation_fingerprint(
        config,
        batch=[first_case],
        rules="rules",
        capture_traces=True,
    )
    second = offline_evaluation_fingerprint(
        config,
        batch=[second_case],
        rules="rules",
        capture_traces=True,
    )
    tasks = HPCSlurmOfflineCheckerExecutor._prepare(
        tmp_path / "repeated-batch",
        fingerprint="fingerprint",
        batch=[first_case, second_case],
        rules="rules",
        capture_traces=True,
    )

    assert first != second
    manifests = [
        json.loads(task.manifest_path.read_text(encoding="utf-8"))
        for task in tasks
    ]
    assert [item["repetition_index"] for item in manifests] == [0, 1]
    assert all("repetition_index" not in item["checker_payload"] for item in manifests)
    batch_manifest = json.loads(
        (tmp_path / "repeated-batch" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert batch_manifest["repetition_indices"] == [0, 1]


def test_stability_collection_preserves_completed_and_marks_slurm_timeout(
    tmp_path,
):
    config = replace(_config(tmp_path), hpc=HPCConfig(max_task_attempts=1))
    executor = HPCSlurmOfflineCheckerExecutor(config)
    batch_dir = tmp_path / "batch"
    completed = TaskFiles(
        index=0,
        instance_id="completed",
        manifest_path=tmp_path / "completed-input.json",
        output_path=tmp_path / "completed-output.json",
        attempts_dir=tmp_path / "completed-attempts",
    )
    timed_out = TaskFiles(
        index=1,
        instance_id="timed-out",
        manifest_path=tmp_path / "timeout-input.json",
        output_path=tmp_path / "timeout-output.json",
        attempts_dir=tmp_path / "timeout-attempts",
    )
    atomic_json(
        completed.manifest_path,
        {"repetition_index": None},
    )
    atomic_json(
        completed.output_path,
        {
            "status": "completed",
            "fingerprint": "fingerprint",
            "instance_id": "completed",
            "checker_output": {
                "predicted_resolved": True,
                "decision_reason": "reason",
                "repository_evidence": [],
                "trajectory": [],
            },
        },
    )
    atomic_json(
        timed_out.attempts_dir / "attempt_01" / "slurm_status.json",
        {"state": "TIMEOUT"},
    )

    results = executor._recover_stability_incomplete(
        batch_dir=batch_dir,
        fingerprint="fingerprint",
        tasks=[completed, timed_out],
    )

    assert isinstance(results[0], CheckerOutput)
    assert isinstance(results[1], CheckerIncompleteOutput)
    assert results[1].failure_category == "timeout"
    assert results[1].terminal_state == "TIMEOUT"
    assert results[1].attempts == 1


def test_offline_checker_retry_script_passes_previous_failed_output(tmp_path):
    config = replace(
        _config(tmp_path),
        hpc=HPCConfig(
            submit=True,
            worker_config_path="configs/gepa_verified_rules.yaml",
        ),
    )
    batch_dir = tmp_path / "batch"

    first = build_offline_checker_array_script(
        config=config,
        batch_dir=batch_dir,
        task_indices=[7],
        attempt=1,
    )
    retry = build_offline_checker_array_script(
        config=config,
        batch_dir=batch_dir,
        task_indices=[7],
        attempt=2,
    )

    assert "--previous-output" not in first
    assert "--previous-output" in retry
    assert "failed_outputs/attempt_01" in retry
    assert "slurm_logs/attempt_02" in retry
    assert "#SBATCH --array=7" in first
    assert "#SBATCH --array=7" in retry
    assert "#SBATCH --array=7%" not in first
    assert "#SBATCH --array=7%" not in retry


def test_offline_checker_submits_complete_array_without_project_throttle(
    tmp_path,
):
    config = replace(
        _config(tmp_path),
        hpc=HPCConfig(
            submit=True,
            worker_config_path="configs/gepa_verified_rules.yaml",
        ),
    )
    indices = list(range(98))

    script = build_offline_checker_array_script(
        config=config,
        batch_dir=tmp_path / "validation",
        task_indices=indices,
        attempt=1,
    )
    array_line = next(
        line for line in script.splitlines() if line.startswith("#SBATCH --array=")
    )

    assert array_line == "#SBATCH --array=" + ",".join(map(str, indices))
    assert "%" not in array_line


def test_offline_checker_worker_gives_new_agent_previous_validator_error(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    rules_path = tmp_path / "rules.md"
    rules_path.write_text("rules", encoding="utf-8")
    manifest_path = tmp_path / "task.json"
    atomic_json(
        manifest_path,
        {
            "fingerprint": "fingerprint",
            "instance_id": "org__repo-1",
            "split": "train",
            "repetition_index": 2,
            "rules_path": str(rules_path),
            "checker_payload": _case(resolved=True).checker_payload(),
        },
    )
    previous_output = tmp_path / "previous.json"
    previous_error = (
        "checker final submission invalid (exit_status=Submitted): "
        r"Invalid \escape: line 3 column 10"
    )
    atomic_json(
        previous_output,
        {
            "status": "agent_failed",
            "failure_kind": "checker_output_contract",
            "error": previous_error,
            "expected_resolved": True,
            "score": 0.0,
            "generated_patch": "must not enter feedback",
        },
    )
    received: list[str] = []

    class FakeChecker:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(
            self,
            case,
            rules,
            *,
            retry_feedback="",
            trajectory_journal_path=None,
        ):
            received.append(retry_feedback)
            return CheckerOutput(
                True,
                "valid",
                (RepositoryEvidence("a.py", "symbol", "finding"),),
                ({"role": "assistant", "content": "submitted"},),
            )

    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.load_optimization_config",
        lambda path: config,
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.configure_docker_capacity",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.DockerChecker",
        FakeChecker,
    )
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()

    assert (
        run_checker_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output_path,
            attempt_dir=attempt_dir,
            previous_output_path=previous_output,
        )
        == 0
    )

    expected_feedback = checker_retry_feedback(previous_error)
    assert received == [expected_feedback]
    assert "expected_resolved" not in received[0]
    assert "generated_patch" not in received[0]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["status"] == "completed"
    assert output["repetition_index"] == 2
    assert output["retry_feedback"] == expected_feedback
    saved = json.loads(
        (attempt_dir / "retry_feedback.json").read_text(encoding="utf-8")
    )
    assert saved["feedback"] == expected_feedback


def test_offline_checker_worker_preserves_failed_contract_trajectory(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    rules_path = tmp_path / "rules.md"
    rules_path.write_text("rules", encoding="utf-8")
    manifest_path = tmp_path / "task.json"
    atomic_json(
        manifest_path,
        {
            "fingerprint": "fingerprint",
            "instance_id": "org__repo-1",
            "split": "train",
            "rules_path": str(rules_path),
            "checker_payload": _case(resolved=True).checker_payload(),
        },
    )

    class FailingChecker:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(
            self,
            case,
            rules,
            *,
            retry_feedback="",
            trajectory_journal_path=None,
        ):
            error = CheckerOutputContractError(
                r"checker final submission invalid: Invalid \escape"
            )
            error.checker_trajectory = (
                {"role": "assistant", "content": "invalid submission"},
            )
            raise error

    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.load_optimization_config",
        lambda path: config,
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.configure_docker_capacity",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.DockerChecker",
        FailingChecker,
    )
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()

    assert (
        run_checker_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output_path,
            attempt_dir=attempt_dir,
        )
        == 1
    )

    failure = json.loads(output_path.read_text(encoding="utf-8"))
    assert failure["status"] == "agent_failed"
    assert failure["failure_kind"] == "checker_output_contract"
    assert failure["failure_stage"] == "checker_execution"
    assert failure["failure_category"] == "output_contract"
    trajectory = json.loads(
        (attempt_dir / "checker_trajectory.json").read_text(encoding="utf-8")
    )
    assert trajectory["messages"][0]["content"] == "invalid submission"


def test_offline_checker_worker_preserves_explicit_agent_timeout(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    rules_path = tmp_path / "rules.md"
    rules_path.write_text("rules", encoding="utf-8")
    manifest_path = tmp_path / "task.json"
    atomic_json(
        manifest_path,
        {
            "fingerprint": "fingerprint",
            "instance_id": "org__repo-1",
            "split": "train",
            "rules_path": str(rules_path),
            "checker_payload": _case(resolved=True).checker_payload(),
        },
    )

    class TimingOutChecker:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(
            self,
            case,
            rules,
            *,
            retry_feedback="",
            trajectory_journal_path=None,
        ):
            error = CheckerAgentTimeout(1800)
            error.checker_trajectory = (
                {"role": "assistant", "content": "still investigating"},
            )
            raise error

    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.load_optimization_config",
        lambda path: config,
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.configure_docker_capacity",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "src.optimization.offline_checker_worker.DockerChecker",
        TimingOutChecker,
    )
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"

    assert (
        run_checker_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output_path,
            attempt_dir=attempt_dir,
        )
        == 1
    )

    failure = json.loads(output_path.read_text(encoding="utf-8"))
    assert failure["status"] == "agent_failed"
    assert failure["failure_kind"] == "checker_agent_timeout"
    assert failure["failure_category"] == "timeout"
    trajectory = json.loads(
        (attempt_dir / "checker_trajectory.json").read_text(encoding="utf-8")
    )
    assert trajectory["messages"][0]["content"] == "still investigating"


def test_offline_checker_recovers_three_evidenced_worker_timeouts(tmp_path):
    config = replace(
        _config(tmp_path),
        checker=replace(_config(tmp_path).checker, agent_timeout_seconds=1800),
        hpc=HPCConfig(max_task_attempts=3),
    )
    executor = HPCSlurmOfflineCheckerExecutor(config)
    batch_dir = tmp_path / "batch"
    task = HPCSlurmOfflineCheckerExecutor._prepare(
        batch_dir,
        fingerprint="fingerprint",
        batch=[_case(resolved=True)],
        rules="rules",
        capture_traces=True,
    )[0]
    for attempt in (1, 2, 3):
        output_path = (
            task.output_path
            if attempt == 3
            else batch_dir
            / "failed_outputs"
            / f"attempt_{attempt:02d}"
            / task.output_path.name
        )
        atomic_json(
            output_path,
            {
                "status": "agent_failed",
                "failure_kind": "checker_agent_timeout",
                "fingerprint": "fingerprint",
                "instance_id": task.instance_id,
            },
        )
        atomic_json(
            task.attempts_dir / f"attempt_{attempt:02d}" / "checker_trajectory.json",
            {"messages": [{"role": "assistant", "content": str(attempt)}]},
        )

    results = executor._recover_evidenced_timeouts(
        batch_dir=batch_dir,
        fingerprint="fingerprint",
        tasks=[task],
    )

    assert len(results) == 1
    assert isinstance(results[0], CheckerTimeoutOutput)
    assert results[0].attempts == 3
    assert results[0].timeout_seconds == 1800
    assert len(results[0].trajectories) == 3

    second_attempt = batch_dir / "failed_outputs" / "attempt_02" / task.output_path.name
    invalid = json.loads(second_attempt.read_text(encoding="utf-8"))
    invalid["failure_kind"] = "operational"
    atomic_json(second_attempt, invalid)
    with pytest.raises(ValueError, match="non-timeout"):
        executor._recover_evidenced_timeouts(
            batch_dir=batch_dir,
            fingerprint="fingerprint",
            tasks=[task],
        )


def test_offline_controller_classifies_slurm_timeout_only_with_agent_journal(
    tmp_path,
):
    config = replace(
        _config(tmp_path),
        hpc=HPCConfig(max_task_attempts=3),
    )
    executor = HPCSlurmOfflineCheckerExecutor(config)
    batch_dir = tmp_path / "batch"
    task = HPCSlurmOfflineCheckerExecutor._prepare(
        batch_dir,
        fingerprint="fingerprint",
        batch=[_case(resolved=True)],
        rules="rules",
        capture_traces=True,
    )[0]
    for attempt in (1, 2, 3):
        attempt_dir = task.attempts_dir / f"attempt_{attempt:02d}"
        atomic_json(
            attempt_dir / "slurm_status.json",
            {
                "instance_id": task.instance_id,
                "task_index": task.index,
                "state": "TIMEOUT",
                "elapsed_seconds": 2100,
            },
        )
        journal = attempt_dir / "checker_trajectory.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"role": "system", "content": "checker"}\n'
            f'{{"role": "assistant", "content": "attempt {attempt}"}}\n',
            encoding="utf-8",
        )

    results = executor._recover_evidenced_timeouts(
        batch_dir=batch_dir,
        fingerprint="fingerprint",
        tasks=[task],
    )

    assert isinstance(results[0], CheckerTimeoutOutput)
    assert results[0].timeout_seconds == 2100
    assert results[0].to_reflection_dict()["trajectory"][-1]["content"] == (
        "attempt 3"
    )

    (task.attempts_dir / "attempt_02" / "checker_trajectory.jsonl").write_text(
        '{"role": "system", "content": "checker"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not show Agent reasoning"):
        executor._recover_evidenced_timeouts(
            batch_dir=batch_dir,
            fingerprint="fingerprint",
            tasks=[task],
        )

    (task.attempts_dir / "attempt_02" / "checker_trajectory.jsonl").write_text(
        '{"role": "assistant", "content": "working"}\n',
        encoding="utf-8",
    )
    status_path = task.attempts_dir / "attempt_02" / "slurm_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["state"] = "OUT_OF_MEMORY"
    atomic_json(status_path, status)
    with pytest.raises(ValueError, match="non-timeout"):
        executor._recover_evidenced_timeouts(
            batch_dir=batch_dir,
            fingerprint="fingerprint",
            tasks=[task],
        )


def test_offline_checker_worker_classifies_input_failure_without_changing_status(
    tmp_path,
):
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"

    assert (
        run_checker_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=tmp_path / "missing-task.json",
            output_path=output_path,
            attempt_dir=attempt_dir,
        )
        == 1
    )

    failure = json.loads(output_path.read_text(encoding="utf-8"))
    assert failure["status"] == "agent_failed"
    assert failure["failure_kind"] == "operational"
    assert failure["failure_stage"] == "input_load"
    assert failure["failure_category"] == "io"


def test_offline_reflection_worker_records_failure_stage_without_reclassifying(
    tmp_path,
    monkeypatch,
):
    manifest_path = tmp_path / "reflection.json"
    atomic_json(
        manifest_path,
        {
            "mode": "offline_reflection",
            "fingerprint": "fingerprint",
            "candidate": {"rules": "rules"},
            "reflective_dataset": {"rules": []},
            "components_to_update": ["rules"],
        },
    )
    monkeypatch.setattr(
        "src.optimization.offline_reflection_worker.load_optimization_config",
        lambda path: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"

    assert (
        run_reflection_task(
            config_path=tmp_path / "config.yaml",
            manifest_path=manifest_path,
            output_path=output_path,
            attempt_dir=attempt_dir,
        )
        == 1
    )

    failure = json.loads(output_path.read_text(encoding="utf-8"))
    assert failure["status"] == "agent_failed"
    assert failure["failure_stage"] == "config_load"
    assert failure["failure_category"] == "runtime"


def test_hpc_reflection_repair_is_a_second_fingerprinted_task(tmp_path):
    config = replace(
        _config(tmp_path),
        hpc=HPCConfig(
            submit=True,
            worker_config_path="configs/gepa_verified_rules.yaml",
        ),
    )
    submissions: list[str] = []

    def submit(script: Path) -> str:
        task_dir = script.parent
        manifest = json.loads((task_dir / "input.json").read_text())
        submissions.append(str(manifest["mode"]))
        if manifest["mode"] == "offline_reflection":
            bundle = (
                task_dir
                / "attempts"
                / "task_0000"
                / "attempt_01"
                / "reflection_inputs"
                / "iteration_0001"
            )
            bundle.mkdir(parents=True)
            atomic_json(bundle / "manifest.json", {"cases": []})
            output = {
                "status": "completed",
                "fingerprint": manifest["fingerprint"],
                "outcome": "repair_required",
                "proposed_rules": "rule for org__repo-1",
                "contamination_hits": [
                    {
                        "kind": "instance_id",
                        "value": "org__repo-1",
                        "instance_id": "org__repo-1",
                    },
                    {
                        "kind": "repository",
                        "value": "org__repo",
                        "instance_id": "org__repo-1",
                    },
                ],
                "evidence_bundle": str(bundle),
                "instance_ids": ["org__repo-1"],
            }
        else:
            output = {
                "status": "completed",
                "fingerprint": manifest["fingerprint"],
                "outcome": "proposal",
                "proposal": {"rules": "general repaired rules"},
            }
        atomic_json(task_dir / "result.json", output)
        return str(100 + len(submissions))

    proposer = HPCOfflineReflectionProposer(config)
    proposer.runtime = SlurmTaskBatch(config.hpc, submitter=submit)
    args = (
        {"rules": "parent rules"},
        {"rules": [{"instance_id": "org__repo-1"}]},
        ["rules"],
    )

    with pytest.raises(ControllerYield):
        proposer(*args)
    with pytest.raises(ControllerYield):
        proposer(*args)
    assert proposer(*args) == {"rules": "general repaired rules"}
    assert submissions == [
        "offline_reflection",
        "offline_reflection_repair",
    ]
    task_states = list(config.run_dir.rglob("task_state.json"))
    assert len(task_states) == 2
    assert all(
        json.loads(path.read_text())["phase"] == "COMPLETE" for path in task_states
    )


def _write_runner_inputs(config: OptimizationConfig) -> None:
    config.initial_rules_path.write_text("rules", encoding="utf-8")
    config.dataset_snapshot.mkdir()
    record = {
        "instance_id": "org__repo-1",
        "split": "train",
        "resolved": True,
        "checker_input": {
            "issue_description": "issue",
            "plan": "plan",
            "repository": {
                "repo": "org/repo",
                "base_commit": "abc",
                "instance_id": "org__repo-1",
            },
        },
        "asi": {
            "plan_trajectory": {},
            "code_trajectory": {},
            "generated_patch": "",
            "evaluator_result": {},
        },
    }
    (config.dataset_snapshot / "train.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    record["split"] = "validation"
    record["instance_id"] = "org__repo-2"
    record["checker_input"]["repository"]["instance_id"] = "org__repo-2"
    (config.dataset_snapshot / "validation.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    atomic_json(
        config.dataset_snapshot / "manifest.json",
        {
            "complete": True,
            "provisional": False,
            "train_instances": 1,
            "validation_instances": 1,
        },
    )


@pytest.mark.parametrize(
    ("backend", "uses_slurm"),
    [("local", False), ("hpc_slurm", True)],
)
def test_offline_backend_is_selected_from_config(
    tmp_path,
    backend,
    uses_slurm,
):
    config = replace(
        _config(tmp_path),
        execution=OfflineExecutionConfig(backend=backend),
        run_dir=tmp_path / f"run-{backend}",
    )
    _write_runner_inputs(config)

    def inspect_optimize(**kwargs):
        assert (kwargs["adapter"].batch_executor is not None) is uses_slurm
        raise ControllerYield(
            batch_dir="test",
            job_id=None,
            reason="selection_checked",
        )

    assert (
        run_optimization(
            config,
            proposer=lambda *args: {"rules": "new rules"},
            optimize_fn=inspect_optimize,
        )
        is None
    )
