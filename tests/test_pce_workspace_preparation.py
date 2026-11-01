"""Real shared executor/Worker/Runner handoff; only external systems are mocked."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts.hpc_runtime import reclaim_disposable_phase_workspaces
from src.exceptions import ControllerYield, FatalError
from src.optimization.hpc.task_batch import SlurmTaskBatch
from src.pce_dataset import repository_pce_dataset
from src.swe_rpg_pce.config import load_swe_rpg_pce_config
from src.swe_rpg_pce.dataset import load_swe_rpg_pce_cases
from src.swe_verified_pce.hpc_executor import SWEVerifiedPCEHPCExecutor, execution_fingerprint
from src.swe_verified_pce.runner import SWEVerifiedPCERunner
from src.swe_verified_pce.worker import run_task
from tests.test_swe_rpg_pce import rpg_pce_config, rpg_result


def prepared_config(tmp_path, native=False, mode="swe_rpg_pce"):
    loader = load_swe_rpg_pce_config
    case_loader = load_swe_rpg_pce_cases
    if mode == "swe_rpg_pce":
        path = rpg_pce_config(tmp_path)
    else:
        from tests.test_optimization.test_safe_pce_smoke_dataflow import _prepared_verified
        from src.optimization.config import load_optimization_config
        from src.swe_verified_pce.config import load_swe_verified_pce_config
        from src.swe_verified_pce.dataset import load_swe_verified_pce_cases
        path = load_optimization_config(_prepared_verified(tmp_path), require_api_keys=False).safe_pce_config
        loader, case_loader = load_swe_verified_pce_config, load_swe_verified_pce_cases
    raw = yaml.safe_load(path.read_text())
    raw.setdefault("execution", {})["workspace_preparation"] = "separate_slurm_v1"
    raw["hpc"].update(max_running_array_tasks=0, worker_config_path=str(path), submit=True)
    if native:
        for role in ("plan", "code"):
            raw[role].update(executor="codex_cli", model="gpt-6-sol", reasoning_effort="high",
                codex_binary="/private/codex", codex_auth_file="/private/auth.json", codex_version="0.160.1")
    path.write_text(yaml.safe_dump(raw))
    config = loader(path, require_api_keys=False)
    config = replace(config, hpc=replace(config.hpc, worker_config_path=str(path), submit=True))
    config.run_dir.mkdir(parents=True, exist_ok=True)
    (config.run_dir / "run_manifest.json").write_text("{}")
    cases = case_loader(config.dataset_snapshot, config.image_manifest)[0][:2]
    return config, cases


def fake_external_systems(monkeypatch, *, code_fails_once=False):
    observations = {"environments": [], "calls": Counter(), "live_trees": []}
    class Environment:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.workspace = Path(kwargs["host_workdir"])
            observations["environments"].append(kwargs)
            if kwargs.get("initialize_host_workdir", True):
                self.workspace.mkdir(parents=True)
                (self.workspace / "baseline.txt").write_text("original SIF")
            else:
                assert (self.workspace / "baseline.txt").exists(), "Worker attempted an empty bind"
            task_root = next(parent for parent in self.workspace.parents if parent.name == "hpc_tasks")
            handoffs = list(task_root.glob("pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))
            phases = list(task_root.glob("*/*/attempts/task_*/attempt_*/workspaces/*"))
            observations["live_trees"].append(len(handoffs) + len(phases))
        def cleanup(self):
            pass
    monkeypatch.setattr("src.swe_verified_pce.runner.ApptainerEnvironment", Environment)
    monkeypatch.setattr("src.swe_verified_pce.worker.configure_docker_capacity", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(SWEVerifiedPCERunner, "_verify_sif", lambda *a: None)
    monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate", lambda *a, **k: (Path("/verified-ancestors.bundle"), {"ancestors": True}))
    def restore(self, env, case, **kwargs):
        (env.workspace / "baseline.txt").write_text("base and ancestors only")
        evidence = kwargs["evidence_dir"]
        evidence.mkdir(parents=True)
        (evidence / "base.json").write_text(json.dumps({"base": case.base_commit}))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_restore_agent_repository", restore)
    monkeypatch.setattr("src.environment.repository_baseline.verify_swebench_evaluator_repository", lambda *a, **k: {})
    monkeypatch.setattr(SWEVerifiedPCERunner, "_capture_code_workspace_evidence", lambda *a, **k: {})
    plan = " \n# Original Plan\n\nNo host reconstruction.\n\n"
    patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    def agent_adapter(function, *, role, case, **kwargs):
        def agent(agent_config, *args, **kwargs):
            observations["calls"][(case.instance_id, role)] += 1
            env = args[-1]
            assert env.kwargs["initialize_host_workdir"] is False
            assert env.kwargs["cwd"] == getattr(case, "working_directory", "/testbed")
            assert (env.workspace / "baseline.txt").read_text() == "base and ancestors only"
            assert "REFERENCE" not in str(agent_config.prompts)
            if role == "plan":
                (env.workspace / "planner-only.txt").write_text("must not reach Code")
                return plan, [{"role": "assistant", "content": "START_PLAN\n" + plan + "END_PLAN"}]
            assert args[0] == plan and not (env.workspace / "planner-only.txt").exists()
            if code_fails_once and observations["calls"][(case.instance_id, role)] == 1:
                raise RuntimeError("synthetic Code interruption")
            return patch, [{"role": "assistant", "content": patch}]
        return agent
    monkeypatch.setattr("src.swe_verified_pce.runner.agent_runner", agent_adapter)
    original_init = SWEVerifiedPCERunner.__init__
    def init(self, config, capacity_window, **kwargs):
        def evaluator(received_patch, case, **options):
            if options.get("evaluation_started_callback"):
                options["evaluation_started_callback"]()
            observations["calls"][(case.instance_id, "evaluate")] += 1
            assert received_patch == patch
            assert options["repository_prepared"] is True
            assert (options["phase_workdir"] / "baseline.txt").read_text() == "original SIF"
            assert not (options["phase_workdir"] / "planner-only.txt").exists()
            result = rpg_result(case.instance_id) if hasattr(case, "working_directory") else {
                "status": "completed", "task_outcome": "resolved", "evaluator_resolved": True,
                "terminal_kind": "official_tests_resolved",
                "official_report": {case.instance_id: {"resolved": True, "tests_status": {"target": "pass"}}}}
            options["result_callback"](result)
            return result
        kwargs["evaluator"] = evaluator
        original_init(self, config, capacity_window, **kwargs)
    monkeypatch.setattr(SWEVerifiedPCERunner, "__init__", init)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    return observations, plan, patch


def scheduler(monkeypatch):
    submissions = []
    def submit(script):
        batch = script.parent
        # Window-local task state uses the original full-batch task authority.
        if not (batch / "tasks").is_dir():
            batch = batch.parent.parent
        manifests = list((batch / "tasks").glob("*.json"))
        config_path = Path(next(line for line in script.read_text().splitlines()
                               if " -m src.swe_verified_pce.worker " in line).split("--config ", 1)[1].split(" ", 1)[0])
        attempt = int(next(line for line in script.read_text().splitlines() if line.startswith("ATTEMPT=")).split("=")[1])
        spec = next(line for line in script.read_text().splitlines() if line.startswith("#SBATCH --array=")).split("=")[1]
        assert "%" not in spec
        indices = [int(value) for value in spec.split(",")]
        operation = json.loads(manifests[0].read_text()).get("operation", "pce")
        submissions.append((operation, indices, attempt, script.read_text()))
        for index in indices:
            run_task(config_path=config_path, task_manifest_path=batch / "tasks" / f"task_{index:04d}.json",
                output_path=batch / "outputs" / f"task_{index:04d}.json",
                attempt_dir=batch / "attempts" / f"task_{index:04d}" / f"attempt_{attempt:02d}",
                checkpoint_dir=batch / "checkpoints" / f"task_{index:04d}", attempt=attempt)
        return str(9000 + len(submissions))
    monkeypatch.setattr("src.optimization.hpc.task_batch.submit_slurm_array", submit)
    # All jobs in this test write atomic outputs synchronously; failed outputs
    # already distinguish failed elements without any scheduler query.
    monkeypatch.setattr("src.optimization.hpc.task_batch.query_slurm_task_status", lambda *a: pytest.fail("unneeded query"))
    return submissions


def finish(executor, cases):
    for _ in range(16):
        try:
            return executor.evaluate(cases)
        except ControllerYield:
            pass
    pytest.fail("workflow did not finish within its configured attempts")


def with_window(config, size):
    raw = yaml.safe_load(config.config_path.read_text())
    raw["execution"]["pce_window_size"] = size
    config.config_path.write_text(yaml.safe_dump(raw))
    return replace(config, pce_window_size=size)


def test_current_pce_has_no_evaluator_marker_write_dependency(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    scheduler(monkeypatch)
    write = Path.write_text
    writes = []
    def fail_marker(path, *args, **kwargs):
        if path.name.startswith("evaluate_started.json"):
            writes.append(path)
            raise PermissionError("obsolete marker is not writable")
        return write(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_marker)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(config.mode)), cases)
    assert all(row["pce_status"] == "completed" for row in results)
    assert all(observations["calls"][(case.instance_id, "evaluate")] == 1 for case in cases)
    assert not writes and not list(config.run_dir.rglob("evaluate_started.json"))


def test_unavailable_post_code_check_cannot_become_a_scored_agent_failure(tmp_path, monkeypatch):
    from src.agents import pce_backend
    from src.swe_verified_pce import runner as module
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    adapter = module.agent_runner
    checks = []
    def code_adapter(function, **context):
        if context["role"] == "plan":
            return adapter(function, **context)
        def code(*args, **kwargs):
            attempt = context["attempt_dir"]
            (attempt / "submission.json").write_text(json.dumps({"code_patch": "original patch", "test_patch": ""}))
            checks.append(attempt)
            env = SimpleNamespace(execute=lambda *a, **k: {"returncode": 127, "output": "sha256sum unavailable"})
            return pce_backend._run_code(None, "Plan", "Task", env, attempt_dir=attempt,
                                         submission_protocol="split_code_test_v1")
        return code
    monkeypatch.setattr(module, "agent_runner", code_adapter)
    trajectory = [{"role": "assistant", "content": "Original Code investigation"}]
    monkeypatch.setattr(pce_backend, "_execute", lambda *a, **k: ("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", trajectory))
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(config.mode))
    result = finish(executor, cases[:1])[0]
    assert result["pce_status"] == "incomplete" and "score" not in result
    assert result["last_worker_output"]["failure_category"] != "agent"
    assert result["last_worker_output"]["failure_trajectory"] == trajectory
    assert len(checks) == 3 and observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert [attempt for op, _, attempt, _ in submissions if op == "pce"] == [1, 2, 3]
    assert finish(executor, cases[:1]) == [result] and len(checks) == 3


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("mode", ["swe_verified_pce", "swe_rpg_pce"])
def test_window8_bounds_live_trees_and_preserves_full_batch_identity(tmp_path, monkeypatch, native, mode):
    config, cases = prepared_config(tmp_path, native, mode)
    config = with_window(config, 8)
    if mode == "swe_rpg_pce":
        assert load_swe_rpg_pce_config(config.config_path, require_api_keys=False).pce_window_size == 8
    observations, plan, patch = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    original = SWEVerifiedPCERunner.prepare_workspace
    live_counts = []
    def prepare(self, case, phase):
        value = original(self, case, phase)
        live_counts.append(len(list(config.run_dir.glob(
            "hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))))
        return value
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(mode))
    # Repetitions cross the window boundary: neither their IDs nor executions
    # may collapse just because two windows contain the same source cases.
    results = finish(executor, cases * 5)
    assert [row["task_index"] for row in results] == list(range(10))
    assert [row["pce_run_index"] for row in results] == [i // 2 + 1 for i in range(10)]
    assert [row["instance_id"] for row in results] == [case.instance_id for case in cases * 5]
    assert all(row["plan"] == plan and row["raw_patch"] == row["patch"] == patch for row in results)
    assert max(live_counts) == 24
    assert max(observations["live_trees"]) == 24
    assert [(op, len(ids)) for op, ids, _, _ in submissions] == [
        ("prepare_workspace", 24), ("pce", 8), ("prepare_workspace", 6), ("pce", 2)]
    assert submissions[-1][1] == [8, 9]
    assert all(count == 5 for count in observations["calls"].values())
    before = {str(p): p.read_bytes() for p in config.run_dir.glob("hpc_tasks/pce/*/outputs/*.json")}
    assert executor.evaluate(cases * 5) == results
    assert len(submissions) == 4
    assert before == {str(p): p.read_bytes() for p in config.run_dir.glob("hpc_tasks/pce/*/outputs/*.json")}
    assert not list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))
    fingerprint = executor.execution_fingerprint(cases * 5)
    authority = config.run_dir / "hpc_tasks/pce" / fingerprint
    assert len(list((authority / "tasks").glob("*.json"))) == 10
    assert len(list((authority / "outputs").glob("*.json"))) == 10
    assert len(list((authority / "checkpoints").glob("*/plan.json"))) == 10
    assert len(list((authority / "execution_windows").glob("*/task_state.json"))) == 2


def test_identical_cases_in_separate_windows_remain_independent(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")),
                     [cases[0], cases[0]])
    assert [row["pce_run_index"] for row in results] == [1, 2]
    assert len({row["fingerprint"] for row in results}) == 1
    assert [row["task_index"] for row in results] == [0, 1]
    assert observations["calls"][(cases[0].instance_id, "plan")] == 2
    assert len(submissions) == 4


def test_next_window_waits_for_running_pce_not_just_preparation(tmp_path, monkeypatch):
    import sys
    from src.optimization.hpc.slurm import SlurmTaskStatus
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    original = run_task
    deferred = []
    def postpone_first_pce(**kwargs):
        manifest = json.loads(kwargs["task_manifest_path"].read_text())
        if manifest.get("operation", "pce") == "pce" and manifest["task_index"] == 0 and not deferred:
            deferred.append(kwargs)
            return 0
        return original(**kwargs)
    monkeypatch.setattr(sys.modules[__name__], "run_task", postpone_first_pce)
    monkeypatch.setattr("src.optimization.hpc.task_batch.query_slurm_task_status",
                        lambda *a: SlurmTaskStatus(state="RUNNING", elapsed_seconds=5))
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce"))
    for _ in range(3):
        with pytest.raises(ControllerYield):
            executor.evaluate(cases)
    assert [op for op, _, _, _ in submissions] == ["prepare_workspace", "pce"]
    assert not observations["calls"]  # No model work has started in window 2.
    assert original(**deferred[0]) == 0
    results = finish(executor, cases)
    assert len(results) == 2 and all(row["status"] == "completed" for row in results)
    assert [op for op, _, _, _ in submissions] == ["prepare_workspace", "pce", "prepare_workspace", "pce"]


def test_window_retry_reclaims_unused_evaluator_tree_before_repreparing(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch, code_fails_once=True)
    submissions = scheduler(monkeypatch)
    original = SWEVerifiedPCERunner.prepare_workspace
    counts = []
    def prepare(self, case, phase):
        value = original(self, case, phase)
        counts.append(len(list(config.run_dir.glob(
            "hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))))
        return value
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases[:1])
    assert results[0]["status"] == "completed" and max(counts) <= 3
    assert observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert observations["calls"][(cases[0].instance_id, "code")] == 2
    assert [attempt for op, _, attempt, _ in submissions if op == "pce"] == [1, 2]
    assert not list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))


def test_preparation_recovers_within_budget_without_synthetic_pce_failure(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch)
    scheduler(monkeypatch)
    original = SWEVerifiedPCERunner.prepare_workspace
    failures = []
    def prepare(self, case, phase):
        if case.instance_id == cases[0].instance_id and phase == "code" and len(failures) < 2:
            failures.append(phase)
            raise OSError("synthetic storage failure")
        return original(self, case, phase)
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases)
    assert [row["status"] for row in results] == ["completed", "completed"]
    assert [row["task_index"] for row in results] == [0, 1]
    assert len(failures) == 2
    assert all(count == 1 for count in observations["calls"].values())
    assert all(row["attempt"] == 1 for row in results)  # No PCE attempt spent on preparation.
    assert not list(config.run_dir.glob("hpc_tasks/pce/*/failed_outputs/*/*.json"))


def test_preparation_exhaustion_is_durable_and_never_a_synthetic_pce_failure(tmp_path, monkeypatch):
    from src.exceptions import confirmed_run_stop
    from src.optimization.hpc.task_batch import TaskAttemptsExhausted, TaskBatchBlocked
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    original = SWEVerifiedPCERunner.prepare_workspace
    def prepare(self, case, phase):
        if case.instance_id == cases[0].instance_id and phase == "code":
            raise OSError("persistent preparation failure")
        return original(self, case, phase)
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    for _ in range(2):
        executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce"))
        with pytest.raises(TaskBatchBlocked) as caught:
            finish(executor, cases)
        assert not isinstance(caught.value, TaskAttemptsExhausted)
        assert confirmed_run_stop(caught.value.failure_record)
        assert caught.value.failure_record["attempt"] == 3
    assert [(ids, attempt) for op, ids, attempt, _ in submissions] == [
        (list(range(6)), 1), ([1], 2), ([1], 3)]
    assert not observations["calls"]
    assert not list(config.run_dir.glob("hpc_tasks/pce/*/outputs/*.json"))
    assert not list(config.run_dir.glob("hpc_tasks/pce/*/windows/*/exhausted_collection.json"))


@pytest.mark.parametrize("mode", ["swe_rpg_pce", "swe_verified_pce"])
@pytest.mark.parametrize("persistent", [False, True])
def test_split_code_retry_reuses_plan_rebuilds_code_and_preserves_rejections(tmp_path, monkeypatch, mode, persistent):
    from src.agents.plan_submission import SPLIT_CODE_PROTOCOL
    from src.swe_verified_pce import runner as module
    from src.swe_rpg_pce.evaluator import SWERPGEvaluatorOperationalError
    config, cases = prepared_config(tmp_path, mode=mode)
    raw = yaml.safe_load(config.config_path.read_text())
    raw["paths"]["code_prompt_source_config"] = str(Path("configs/prompts/safe_pce_code_split_v1.yaml").resolve())
    raw["code"]["submission_protocol"] = SPLIT_CODE_PROTOCOL
    config.config_path.write_text(yaml.safe_dump(raw))
    config = repository_pce_dataset(mode).load_config(config.config_path, require_api_keys=False)
    config = with_window(config, 1)
    assert config.code_submission_protocol == SPLIT_CODE_PROTOCOL
    observations, plan, patch = fake_external_systems(monkeypatch)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_capture_code_workspace_evidence",
                        lambda *a, **k: pytest.fail("split output must not recapture either patch"))
    submissions = scheduler(monkeypatch)
    test_patch = "diff --git a/tests.py b/tests.py\n" + "test diagnostic\r\n" * 1000
    adapter = module.agent_runner
    feedbacks = []
    def split_adapter(function, **kwargs):
        original = adapter(function, **kwargs)
        if kwargs["role"] != "code":
            return original
        def code(*args, **options):
            assert options["submission_protocol"] == SPLIT_CODE_PROTOCOL
            feedback = options.get("code_retry_context")
            if kwargs["case"].instance_id == cases[0].instance_id:
                feedbacks.append(feedback)
            value, trajectory = original(*args, **options)
            if feedback:
                data = json.loads(feedback)
                assert set(data) == {"feedback", "code_patch", "test_patch"}
                assert data["code_patch"] == patch and data["test_patch"] == test_patch
                assert "HIDDEN_OFFICIAL_TEST" not in feedback
            return {"code_patch": value, "test_patch": test_patch}, trajectory
        return code
    monkeypatch.setattr(module, "agent_runner", split_adapter)
    original_init = SWEVerifiedPCERunner.__init__
    evaluations = Counter()
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        evaluate = self.evaluator
        def maybe_reject(received, case, **options):
            assert received == patch and test_patch not in str(options)
            evaluations[case.instance_id] += 1
            if case.instance_id == cases[0].instance_id and (persistent or evaluations[case.instance_id] == 1):
                evidence = {"terminal_kind": "code_patch_not_applied", "task_outcome": "unresolved",
                    "stage_exit_codes": {"setup": 0, "test_patch": 0, "code_patch": 1},
                    "code_patch_attempts": [{"preflight_returncode": 1, "apply_returncode": None}],
                    "raw_test_output": "HIDDEN_OFFICIAL_TEST.py:337"}
                if mode == "swe_rpg_pce":
                    raise SWERPGEvaluatorOperationalError("patch application failed",
                        outcome_reason="rpg_code_patch_not_applied", retry_disposition="retry_code", evidence=evidence)
                options["result_callback"](evidence)
                pytest.fail("a rejected Code patch was checkpointed as a scored evaluation")
            return evaluate(received, case, **options)
        self.evaluator = maybe_reject
    monkeypatch.setattr(SWEVerifiedPCERunner, "__init__", init)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(mode))
    results = finish(executor, cases)
    expected = 3 if persistent else 2
    assert observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert observations["calls"][(cases[0].instance_id, "code")] == expected
    assert evaluations[cases[0].instance_id] == expected
    assert feedbacks[0] is None and all(feedbacks[1:])
    assert results[0]["plan"] == plan
    rejected = results[0]["code_rejections"]
    assert len(rejected) == (3 if persistent else 1)
    assert len({item["checkpoint_sha256"] for item in rejected}) == len(rejected)
    assert all(item["submission"]["patch"] == patch and item["submission"]["test_patch"] == test_patch
               for item in rejected)
    assert all("HIDDEN_OFFICIAL_TEST" in item["evaluator_evidence"]["raw_test_output"] for item in rejected)
    if persistent:
        assert results[0]["pce_status"] == "agent_failed"
        assert results[0]["agent_failure_attempts"] == [1, 2, 3]
        assert results[0]["patch"] == ""  # Rejected submissions are evidence, not a valid final patch.
    else:
        assert results[0]["pce_status"] == "completed"
        assert results[0]["test_patch"] == test_patch
    assert results[1]["pce_status"] == "completed" and results[1]["test_patch"] == test_patch
    assert [attempt for op, ids, attempt, _ in submissions if op == "pce" and ids == [0]] == list(range(1, expected + 1))
    handoffs = list(config.run_dir.glob("hpc_tasks/pce/*/attempts/task_0000/attempt_*/workspace_preparation.json"))
    assert len(handoffs) == expected
    for path in handoffs:
        phases = json.loads(path.read_text())["phases"]
        assert phases["code"]["workspace_status"] == "ready"
        if path.parent.name != "attempt_01":
            assert phases["plan"]["workspace_status"] == "checkpoint_reused"
    before = list(submissions)
    assert finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(mode)), cases) == results
    assert submissions == before
    assert not list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))
    from src.optimization.pce_reflector_evidence import pce_reflector_views
    record = {"pce_runs": [{"pce_output": results[0]}]}
    original = json.dumps(record)
    views, index = pce_reflector_views(record, "raw.json")
    rejection_view = views[index["runs"][0]["code_rejections"]]
    assert rejection_view[0]["submission"]["test_patch"] == test_patch
    assert rejection_view[0]["submission"]["trajectory"] == rejected[0]["submission"]["trajectory"]
    assert json.dumps(record) == original
    assert not list(config.run_dir.rglob("code_workspace_evidence.json"))
    assert not list(config.run_dir.rglob("unstaged_diagnostic_changes.patch"))


def test_evaluator_exhaustion_reuses_original_code_and_stops_after_three(tmp_path, monkeypatch):
    from src.exceptions import confirmed_run_stop
    from src.swe_rpg_pce.evaluator import SWERPGEvaluatorOperationalError
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, plan, patch = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    original_init = SWEVerifiedPCERunner.__init__
    seen = []
    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        evaluate = self.evaluator
        def failing_evaluation(received, case, **options):
            if case.instance_id == cases[0].instance_id:
                seen.append(received)
                raise SWERPGEvaluatorOperationalError("code_patch_conflict",
                    outcome_reason="code_patch_failed", retry_disposition="retry_same_phase", evidence={
                    "stage_exit_codes": {"setup": 0, "test_patch": 0, "code_patch": 1}})
            return evaluate(received, case, **options)
        self.evaluator = failing_evaluation
    monkeypatch.setattr(SWEVerifiedPCERunner, "__init__", init)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce"))
    results = finish(executor, cases)
    assert [row["pce_status"] for row in results] == ["incomplete", "completed"]
    failed = results[0]
    assert confirmed_run_stop(failed["failure_record"])
    assert failed["last_worker_output"]["error"] == "code_patch_conflict"
    assert failed["last_worker_output"]["attempt"] == failed["attempts_exhausted"] == 3
    assert seen == [patch] * 3
    assert all(count == 1 for count in observations["calls"].values())
    checkpoints = {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce/*/checkpoints/*/*.json")}
    count = len(submissions)
    assert finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases) == results
    assert len(submissions) == count and seen == [patch] * 3
    assert checkpoints == {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce/*/checkpoints/*/*.json")}
    payloads = [json.loads(raw)["payload"] for raw in checkpoints.values()]
    assert any(payload.get("plan") == plan for payload in payloads)
    assert any(payload.get("patch") == patch for payload in payloads)
    assert [attempt for op, ids, attempt, _ in submissions if op == "pce" and ids == [0]] == [1, 2, 3]


@pytest.mark.parametrize("malformed_json", [False, True])
def test_exhausted_invalid_completion_is_not_reused_as_a_success(tmp_path, malformed_json):
    from src.optimization.hpc.task_batch import TaskFiles, atomic_json
    config, cases = prepared_config(tmp_path)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce"))
    tasks = [TaskFiles(index=i, instance_id=case.instance_id,
                      manifest_path=tmp_path / f"in-{i}.json", output_path=tmp_path / f"out-{i}.json",
                      attempts_dir=tmp_path / f"attempts-{i}") for i, case in enumerate(cases)]
    for task in tasks:
        atomic_json(task.manifest_path, {"pce_run_index": 1})
        atomic_json(task.output_path, {"status": "completed", "original": task.instance_id})
    if malformed_json:
        tasks[0].output_path.write_text('{"status": "completed", "cut off":')
    slurm_status = tasks[0].attempts_dir / "attempt_03/slurm_status.json"
    slurm_status.parent.mkdir(parents=True)
    slurm_status.write_text('{"also cut off":')
    original = [task.output_path.read_bytes() for task in tasks]
    record = {"failure_kind": "task_attempts_exhausted", "attempt": 3, "max_attempts": 3,
              "details": [{"instance_id": tasks[0].instance_id, "output_path": str(tasks[0].output_path)}]}
    results = executor._collect_exhausted(tmp_path, "fp", tasks, failure_record=record)
    assert results[0]["pce_status"] == "incomplete"
    assert results[0]["failure_record"] == record
    if malformed_json:
        assert results[0]["last_worker_output"] is None
        assert "JSONDecodeError" in results[0]["last_worker_output_read_error"]
    else:
        assert results[0]["last_worker_output"] == json.loads(original[0])
    assert results[1] == json.loads(original[1])
    assert [task.output_path.read_bytes() for task in tasks] == original


@pytest.mark.parametrize("infrastructure_first", [False, True])
def test_exhausted_coder_retries_do_not_leave_trees_for_next_window(tmp_path, monkeypatch, infrastructure_first):
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    from src.swe_verified_pce import runner as module
    from src.exceptions import AgentTaskError
    adapter = module.agent_runner
    failures = []
    def failing_adapter(function, **kwargs):
        original = adapter(function, **kwargs)
        if kwargs["role"] == "code" and kwargs["case"].instance_id == cases[0].instance_id:
            def fail(*a, **k):
                failures.append(len(failures) + 1)
                if infrastructure_first and len(failures) == 1:
                    raise OSError("synthetic transient infrastructure failure")
                raise AgentTaskError("synthetic rejected Code", phase="code", reason="invalid_submission",
                                     trajectory=[{"role": "assistant", "content": "original invalid patch"}])
            return fail
        return original
    monkeypatch.setattr(module, "agent_runner", failing_adapter)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases)
    assert results[1]["status"] == "completed"
    if infrastructure_first:
        assert results[0]["pce_status"] == "incomplete"  # Only two Agent failures: no fabricated zero.
        assert results[0]["failure_record"]["max_attempts"] == 3
        assert results[0]["last_worker_output"]["attempt"] == 3
    else:
        assert results[0]["pce_status"] == "agent_failed"
        assert results[0]["agent_failure_attempts"] == [1, 2, 3]
        assert results[0]["code_trajectory"][-1]["content"] == "original invalid patch"
    assert observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert [row["task_index"] for row in results] == [0, 1]
    assert [attempt for op, ids, attempt, _ in submissions if op == "pce" and ids == [0]] == [1, 2, 3]
    assert not list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))


def test_capacity_reclamation_yields_and_resumes_without_extra_model_attempt(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    config = with_window(config, 1)
    observations, _, _ = fake_external_systems(monkeypatch, code_fails_once=True)
    submissions = scheduler(monkeypatch)
    from src.swe_verified_pce import workspace_preparation as module
    original = module.shutil.rmtree
    refused = []
    def refuse_once(path, *args, **kwargs):
        if Path(path).name == "prepared_repository" and not refused:
            refused.append(str(path))
            raise OSError("synthetic transient cleanup error")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(module.shutil, "rmtree", refuse_once)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce"))
    reasons = []
    for _ in range(16):
        try:
            results = executor.evaluate(cases[:1])
            break
        except ControllerYield as exc:
            reasons.append(exc.reason)
    else:
        pytest.fail("cleanup recovery did not finish")
    assert "waiting_for_workspace_capacity_reclamation" in reasons
    assert results[0]["status"] == "completed"
    assert observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert observations["calls"][(cases[0].instance_id, "code")] == 2
    assert [attempt for op, _, attempt, _ in submissions if op == "pce"] == [1, 2]


@pytest.mark.parametrize("value", [-1, True, 1.5, "8"])
def test_window_size_rejects_invalid_config(tmp_path, value):
    config, _ = prepared_config(tmp_path)
    with_window(config, value)
    with pytest.raises(ValueError, match="pce_window_size"):
        load_swe_rpg_pce_config(config.config_path, require_api_keys=False)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("mode", ["swe_verified_pce", "swe_rpg_pce"])
def test_real_wave_precedes_pce_and_handoff_survives_idle_cleanup(tmp_path, monkeypatch, native, mode):
    config, cases = prepared_config(tmp_path, native, mode)
    observations, plan, patch = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    executor = SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset(mode))
    with pytest.raises(ControllerYield):
        executor.evaluate(cases)
    assert [(op, ids) for op, ids, _, _ in submissions] == [("prepare_workspace", list(range(6)))]
    assert not observations["calls"]
    assert "ENV_FILE=" not in submissions[0][3] and "source " not in submissions[0][3]
    handoffs = list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))
    assert len(handoffs) == 6
    reclaim_disposable_phase_workspaces(str(config.run_dir))
    assert all(path.is_dir() for path in handoffs)
    results = finish(executor, cases)
    assert len(results) == 2 and all(row["status"] == "completed" for row in results)
    assert [op for op, _, _, _ in submissions] == ["prepare_workspace", "pce"]
    assert submissions[-1][2] == 1  # Preparation consumed no PCE attempt.
    for result in results:
        assert result["plan"] == plan and result["patch"] == result["raw_patch"] == patch
        receipt = config.run_dir / result["attempt_evidence_relative_path"] / "workspace_preparation.json"
        assert set(json.loads(receipt.read_text())["phases"]) == {"plan", "code", "evaluate"}
        assert "workspace_preparation" not in result  # Host audit, not a new learning input.
    assert not any(path.exists() for path in handoffs)
    assert sum(bool(env["initialize_host_workdir"]) for env in observations["environments"]) == 6
    assert all(count == 1 for count in observations["calls"].values())
    assert all(env["isolate_tmp"] is True for env in observations["environments"])
    cached = {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce/*/checkpoints/*/*.json")}
    assert executor.evaluate(cases) == results
    assert len(submissions) == 2
    assert cached == {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce/*/checkpoints/*/*.json")}


def test_preparation_retries_only_failed_element_and_does_not_suppress_other_case(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    submissions = scheduler(monkeypatch)
    original = SWEVerifiedPCERunner.prepare_workspace
    calls = Counter()
    def prepare(self, case, phase):
        calls[(case.instance_id, phase)] += 1
        if case.instance_id == cases[0].instance_id and phase == "code" and calls[(case.instance_id, phase)] <= 2:
            raise OSError("synthetic setup failure")
        return original(self, case, phase)
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases)
    assert [row["status"] for row in results] == ["completed", "completed"]
    assert [ids for op, ids, _, _ in submissions if op == "prepare_workspace"] == [list(range(6))] + [[1]] * 2
    assert [(ids, attempt) for op, ids, attempt, _ in submissions if op == "pce"] == [([0, 1], 1)]
    assert all(count == 1 for count in observations["calls"].values())
    assert calls[(cases[0].instance_id, "code")] == 3
    assert all(count == 1 for key, count in calls.items() if key != (cases[0].instance_id, "code"))


def test_pce_retry_reuses_successful_plan_and_prepares_only_missing_phases(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, plan, _ = fake_external_systems(monkeypatch, code_fails_once=True)
    submissions = scheduler(monkeypatch)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases[:1])
    assert results[0]["plan"] == plan
    assert observations["calls"][(cases[0].instance_id, "plan")] == 1
    assert observations["calls"][(cases[0].instance_id, "code")] == 2
    assert [attempt for op, _, attempt, _ in submissions if op == "pce"] == [1, 2]
    prep_outputs = [json.loads(path.read_text()) for path in config.run_dir.glob("hpc_tasks/pce_prepare/*/outputs/*.json")]
    assert sum(row["workspace_status"] == "checkpoint_reused" for row in prep_outputs) == 1


def test_terminal_cleanup_includes_unconsumed_handoffs_only_when_explicit(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    fake_external_systems(monkeypatch)
    scheduler(monkeypatch)
    with pytest.raises(ControllerYield):
        SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")).evaluate(cases[:1])
    roots = list(config.run_dir.glob("hpc_tasks/pce_prepare/*/attempts/task_*/attempt_*/prepared_repository"))
    before = {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce_prepare/*/outputs/*.json")}
    report = reclaim_disposable_phase_workspaces(str(config.run_dir), include_prepared=True)
    assert report["removed"] >= 3 and not any(path.exists() for path in roots)
    assert before == {str(path): path.read_bytes() for path in config.run_dir.glob("hpc_tasks/pce_prepare/*/outputs/*.json")}


def test_configuration_switch_is_explicit_and_enters_fingerprint(tmp_path):
    config, cases = prepared_config(tmp_path)
    assert execution_fingerprint(config, cases, semantic_sha256="fixed") == execution_fingerprint(config, cases, semantic_sha256="fixed")
    from src.swe_verified_pce.hpc_executor import pce_semantic_sha256
    a = pce_semantic_sha256(config, include_swebench=False, third_party_identity={})
    b = pce_semantic_sha256(replace(config, workspace_preparation="inline"), include_swebench=False, third_party_identity={})
    assert a != b
    c = pce_semantic_sha256(replace(config, pce_window_size=8), include_swebench=False, third_party_identity={})
    assert a != c


def test_missing_handoff_cannot_fall_back_to_inline_copy(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    from src.swe_rpg_pce.runner import SWERPGPCERunner
    runner = SWERPGPCERunner(config, None, checkpoint_dir=tmp_path / "checkpoint", checkpoint_identity="expected",
                            attempt_dir=config.run_dir / "attempt")
    with pytest.raises(FileNotFoundError):
        runner.run(cases[0])
    assert not observations["environments"] and not observations["calls"]


def test_preparation_does_not_require_model_credentials(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    scheduler(monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ControllerYield):
        SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")).evaluate(cases[:1])
    outputs = list(config.run_dir.glob("hpc_tasks/pce_prepare/*/outputs/*.json"))
    assert len(outputs) == 3
    assert all(json.loads(path.read_text())["status"] == "completed" for path in outputs)
    assert not observations["calls"]


def test_preparation_worker_rejects_changed_parent_case_before_model(tmp_path, monkeypatch):
    config, cases = prepared_config(tmp_path)
    observations, _, _ = fake_external_systems(monkeypatch)
    scheduler(monkeypatch)
    with pytest.raises(ControllerYield):
        SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")).evaluate(cases[:1])
    manifest_path = next(config.run_dir.glob("hpc_tasks/pce_prepare/*/tasks/task_0000.json"))
    value = json.loads(manifest_path.read_text())
    value["case"] = cases[1].to_dict()
    manifest_path.write_text(json.dumps(value))
    output = manifest_path.parent.parent / "outputs" / "tampered.json"
    assert run_task(config_path=config.config_path, task_manifest_path=manifest_path, output_path=output,
        attempt_dir=manifest_path.parent.parent / "attempts/task_0000/attempt_02",
        checkpoint_dir=manifest_path.parent.parent / "checkpoints/task_0000", attempt=2) == 1
    assert json.loads(output.read_text())["status"] == "blocking_failed"
    assert not observations["calls"]


def test_atomic_handoff_preserves_real_git_repository_and_rejects_wrong_identity(tmp_path):
    from src.swe_verified_pce.workspace_preparation import claim_workspace
    config, cases = prepared_config(tmp_path)
    source = config.run_dir / "hpc_tasks/pce_prepare/fingerprint/attempts/task_0000/attempt_01/prepared_repository"
    source.mkdir(parents=True)
    (source / "source.py").write_bytes(b"# frozen bytes\n")
    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True).stdout
    git("init", "-q")
    git("add", "source.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
    head = git("rev-parse", "HEAD")
    attempt = config.run_dir / "hpc_tasks/pce/parent/attempts/task_0000/attempt_01"
    attempt.mkdir(parents=True)
    case = cases[0]
    receipt = {"schema_version": 1, "checkpoint_identity": "wrong", "instance_id": case.instance_id,
        "base_commit": case.base_commit, "sif_sha256": case.image.sif_sha256,
        "target_attempt_relative_path": attempt.relative_to(config.run_dir).as_posix(),
        "phases": {"plan": {"workspace_status": "ready", "prepared_repository_relative_path": source.relative_to(config.run_dir).as_posix()}}}
    path = attempt / "workspace_preparation.json"
    path.write_text(json.dumps(receipt))
    runner = SimpleNamespace(config=config, attempt_dir=attempt, checkpoint_identity="expected")
    target = attempt / "workspaces/plan"
    with pytest.raises(FatalError, match="identity mismatch"):
        claim_workspace(runner, case, "plan", target)
    assert source.is_dir() and not target.exists()
    receipt["checkpoint_identity"] = "expected"
    path.write_text(json.dumps(receipt))
    claim_workspace(runner, case, "plan", target)
    assert not source.exists() and (target / "source.py").read_bytes() == b"# frozen bytes\n"
    assert subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], check=True, capture_output=True).stdout == head
    assert subprocess.run(["git", "-C", str(target), "status", "--porcelain"], check=True, capture_output=True).stdout == b""
