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
    observations = {"environments": [], "calls": Counter()}
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
    def prepare(self, case, phase):
        if case.instance_id == cases[0].instance_id and phase == "code":
            raise OSError("synthetic setup failure")
        return original(self, case, phase)
    monkeypatch.setattr(SWEVerifiedPCERunner, "prepare_workspace", prepare)
    results = finish(SWEVerifiedPCEHPCExecutor(config, dataset=repository_pce_dataset("swe_rpg_pce")), cases)
    assert [row["status"] for row in results] == ["incomplete", "completed"]
    assert results[0]["task_outcome"] == "unknown" and results[0]["final_validation_label"] is None
    assert [ids for op, ids, _, _ in submissions if op == "prepare_workspace"] == [list(range(6)), [1], [1]]
    assert [(ids, attempt) for op, ids, attempt, _ in submissions if op == "pce"] == [([1], 1)]
    assert not any(identifier == cases[0].instance_id for identifier, _ in observations["calls"])
    from src.optimization.safe_pce_learning import outcome_score, IncompletePCEEvaluation
    with pytest.raises(IncompletePCEEvaluation):
        outcome_score(results[0], mode="swe_rpg_pce", instance_id=cases[0].instance_id)


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
