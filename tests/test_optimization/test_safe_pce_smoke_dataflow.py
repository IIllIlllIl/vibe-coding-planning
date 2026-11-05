"""Real Verified Worker + ACE phase wiring; only external boundaries are mocked."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.tools.audit_safe_pce_ace_dataflow import audit
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_preparation import prepare
from src.optimization.behavioral_hybrid_runtime import run_hybrid_optimization
from tests.test_swe_verified_pce_pcce import _snapshot, _row, canonical_image_ref
from tests.test_optimization.test_behavioral_hybrid_runtime import review
from tests.test_polybench_pce import _diff


def _prepared_verified(tmp_path):
    source, images, _ = _snapshot(tmp_path)
    image = next(iter(json.loads(images.read_text())["records"].values()))
    ids = [f"owner__repo-{index}" for index in range(1, 4)]
    wrappers, records = [], {}
    for identifier in ids:
        row = _row(identifier)
        wrappers.append({"instance_id": identifier, "source_row": row,
            "row_sha256": hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        records[canonical_image_ref(identifier)] = dict(image, instance_id=identifier)
    rows = source / "instances.jsonl"
    rows.write_text("".join(json.dumps(row) + "\n" for row in wrappers))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest.update(instances=3, instances_sha256=hashlib.sha256(rows.read_bytes()).hexdigest())
    (source / "manifest.json").write_text(json.dumps(manifest))
    images.write_text(json.dumps({"records": records,
        "source_manifest_sha256": hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()}))
    raw = yaml.safe_load(Path("configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml").read_text())
    raw["paths"].update(dataset_snapshot=str(source), image_manifest=str(images), selection_manifest=None)
    pce = tmp_path / "pce.yaml"
    pce.write_text(yaml.safe_dump(raw))
    path = tmp_path / "learning.yaml"
    prepare(learning_config=Path("configs/gepa_swe_chat_hybrid_formal8_high_v1_20261016.yaml"),
            pce_config=pce, selection={"train": ids[:2], "validation": ids[2:]},
            snapshot=tmp_path / "snapshot", run_dir=tmp_path / "run", output_config=path,
            max_iterations=1, minibatch_size=2, max_metric_calls=6)
    return path


@pytest.mark.parametrize("dataset_mode", ["swe_verified_pce", "swe_rpg_pce"])
@pytest.mark.parametrize("persistent_output_link", [False, True])
@pytest.mark.parametrize("metadata_failure", [False, True])
@pytest.mark.parametrize("workspace_inventory_failure", [False, True])
@pytest.mark.parametrize("learning_mode", ["independent", "codex_reflection"])
def test_verified_end_to_end_and_audit_detects_lost_evidence(
        tmp_path, monkeypatch, dataset_mode, persistent_output_link, metadata_failure, workspace_inventory_failure, learning_mode):
    from src.optimization import behavioral_hybrid_runtime as runtime
    from src.optimization.hpc.task_batch import SlurmTaskBatch
    from src.optimization.behavioral_hybrid_worker import run_task as phase_worker
    from src.swe_verified_pce.worker import run_task as pce_worker
    from src.swe_verified_pce.runner import SWEVerifiedPCERunner
    if dataset_mode == "swe_verified_pce":
        path = _prepared_verified(tmp_path)
    else:
        from tests.test_swe_rpg_pce import rpg_pce_config
        pce_path = rpg_pce_config(tmp_path)
        pce_raw = yaml.safe_load(pce_path.read_text())
        pce_raw["paths"]["code_prompt_source_config"] = str(Path("configs/prompts/safe_pce_code_split_v1.yaml").resolve())
        pce_path.write_text(yaml.safe_dump(pce_raw))
        path = tmp_path / "learning.yaml"
        prepare(learning_config=Path("configs/gepa_swe_chat_hybrid_formal8_high_v1_20261016.yaml"),
            pce_config=pce_path, selection={"train": ["owner__repo-1", "owner__repo-2"], "validation": ["owner__repo-3"]},
            snapshot=tmp_path / "learning-snapshot", run_dir=tmp_path / "run", output_config=path,
            max_iterations=1, minibatch_size=2, max_metric_calls=6)
    if learning_mode == "codex_reflection":
        raw = yaml.safe_load(path.read_text())
        raw["hybrid"].update(learning_mode=learning_mode, refiner_enabled=False,
            curator_model={"executor": "codex_cli", "model": "gpt-6-sol", "reasoning_effort": "high"},
            prompt_bundle="configs/prompts/safe_pce_reflection_v1.yaml")
        path.write_text(yaml.safe_dump(raw))
    if persistent_output_link:
        persistent = tmp_path / "run_state" / "output" / "dataset" / "smoke"
        persistent.mkdir(parents=True)
        staged = tmp_path / "staging" / "output" / "dataset" / "smoke"
        staged.parent.mkdir(parents=True)
        staged.symlink_to(persistent, target_is_directory=True)
        raw = yaml.safe_load(path.read_text())
        raw["paths"]["run_dir"] = str(staged)
        path.write_text(yaml.safe_dump(raw))
    config = load_optimization_config(path, require_api_keys=False)
    if persistent_output_link:
        assert config.run_dir == staged and config.run_dir.is_symlink()
    if metadata_failure:
        from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
        original_open = Path.open
        def metadata_denied(path, *args, **kwargs):
            if path.name in {PRIVATE_ARTIFACT_MARKER, ".gitignore"}:
                raise PermissionError("synthetic metadata-only write failure")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", metadata_denied)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda config: None)
    monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate", lambda *a, **kw: (tmp_path / "bundle", {}))
    monkeypatch.setattr("src.swe_verified_pce.worker.configure_docker_capacity", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(SWEVerifiedPCERunner, "_verify_sif", lambda *a: None)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_ensure_repository_history", lambda *a: (tmp_path / "bundle", {}))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_restore_agent_repository", lambda *a, **kw: None)
    def execute(command, **kwargs):
        if command == "git ls-files --others --exclude-standard" and workspace_inventory_failure:
            return {"returncode": 1, "output": "synthetic optional inventory failure"}
        if command == "git diff --cached --binary --full-index":
            return {"returncode": 0, "output": _diff("src/module.py")}
        return {"returncode": 0, "output": ""}
    monkeypatch.setattr(SWEVerifiedPCERunner, "_environment", lambda *a, **kw:
        SimpleNamespace(cleanup=lambda: None, execute=execute))
    agent_calls = {"plan": 0, "code": 0}
    test_patch = "diff --git a/tests.py b/tests.py\n" + "preserved diagnostics\r\n" * 1000
    plan = " \n# Plan\n\n```python\nx = 1\n```\n" + ("Evidence.\n" * 4000) + "Rule 1: inspect requirements.\nDecisive tail.\n\n"
    def planner(agent_config, issue, env, **kw):
        agent_calls["plan"] += 1
        assert "plan-00001" not in kw["planning_rules"]
        return plan, [{"role": "user", "content": issue + "\n" + kw["planning_rules"]},
                      {"role": "assistant", "content": "START_PLAN\n" + plan + "END_PLAN"}]
    def coder(agent_config, received, issue, env, **kw):
        agent_calls["code"] += 1
        assert received == plan
        assert "planning_rules" not in kw
        submission = _diff("src/module.py")
        if dataset_mode == "swe_rpg_pce":
            assert kw["submission_protocol"] == "split_code_test_v1"
            submission = {"code_patch": submission, "test_patch": test_patch}
        return submission, [{"role": "user", "content": "Issue\n" + received}]
    monkeypatch.setattr("src.swe_verified_pce.runner.plan_agent.run", planner)
    monkeypatch.setattr("src.swe_verified_pce.runner.code_agent.run", coder)
    original = SWEVerifiedPCERunner.run
    def run(self, case):
        resolved = "revision" in self.config.planning_rules
        if dataset_mode == "swe_rpg_pce":
            from tests.test_swe_rpg_pce import rpg_result
            self.evaluator = lambda *a, **kw: rpg_result(case.instance_id, resolved)
        else:
            self.evaluator = lambda *a, **kw: {"status": "completed",
                "terminal_kind": "official_tests_resolved" if resolved else "official_tests_unresolved",
                "evaluator_resolved": resolved, "task_outcome": "resolved" if resolved else "unresolved",
                "official_report": {case.instance_id: {"resolved": resolved, "tests_status": {"FAIL_TO_PASS": {"success": ["x"]}}}}}
        return original(self, case)
    monkeypatch.setattr(SWEVerifiedPCERunner, "run", run)
    def agent(**kw):
        root = Path(kw["evidence_dir"])
        manifest = json.loads((root / "manifest.json").read_text())
        from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
        assert not list(root.rglob(PRIVATE_ARTIFACT_MARKER))
        assert not list(root.rglob(".gitignore"))
        assert PRIVATE_ARTIFACT_MARKER not in json.dumps(manifest)
        assert PRIVATE_ARTIFACT_MARKER not in kw["system"]
        assert PRIVATE_ARTIFACT_MARKER not in kw["instance_template"]
        role = manifest["role"]
        parent = json.loads((root / "counted_playbook.json").read_text())
        if role == "reflector":
            record = json.loads((root / "record.json").read_text())
            if dataset_mode == "swe_rpg_pce":
                runs = json.loads((root / record["fields"]["pce_runs"]).read_text())
                assert all(run["pce_output"]["test_patch"] == test_patch for run in runs)
                required_tests = [name for name in manifest["required_files"] if name.endswith("/test_patch.json")]
                assert required_tests
                assert all(json.loads((root / name).read_text()) == test_patch for name in required_tests)
            return review(parent, record), []
        assert role == "curator"
        records = json.loads((root / "records.json").read_text())
        return {"reasoning": "Evidence-supported revision", "operations": [{"type": "UPDATE",
            "target_id": parent["checklist"][0]["id"], "content": {
                "condition": parent["checklist"][0]["condition"], "guide": "Compare the requirements; revision."},
            "supporting_instance_ids": [records[0]["instance_id"]]}]}, []
    monkeypatch.setattr(runtime, "run_evidence_reflector", agent)
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    if learning_mode == "codex_reflection":
        from src.optimization import safe_pce_reflection
        class Conversation:
            def __init__(self, **kwargs): self.root = kwargs["container"].evidence_dir
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def call_json(self, **kwargs):
                parent = json.loads((self.root / "parent.json").read_text())
                parent["checklist"][0]["guide"] = "Compare the requirements; revision."
                (self.root.parent / "candidate_response.json").write_text(json.dumps(parent))
                return parent, []
            def session_evidence(self): return []
        original_reflection = safe_pce_reflection.run_reflection_phase
        monkeypatch.setattr(safe_pce_reflection, "run_reflection_phase", lambda *args, **kwargs:
            original_reflection(*args, **kwargs, conversation_factory=Conversation))
    waves = []
    def scheduler(self, **kw):
        tasks = kw["tasks"]
        kw["write_script"]([task.index for task in tasks], 1)
        outputs = []
        waves.append(len(tasks))
        for task in tasks:
            manifest = json.loads(task.manifest_path.read_text())
            arguments = dict(config_path=path, manifest_path=task.manifest_path, output_path=task.output_path,
                             attempt_dir=task.attempts_dir / "attempt_01")
            if manifest["mode"] in {"swe_verified_pce", "swe_rpg_pce"}:
                arguments.pop("manifest_path")
                arguments.update(config_path=self.hpc.worker_config_path,
                    task_manifest_path=task.manifest_path, checkpoint_dir=kw["batch_dir"] / "checkpoints" / task.manifest_path.stem,
                    attempt=1)
                code = pce_worker(**arguments)
            else:
                code = phase_worker(**arguments)
            assert code == 0, task.output_path.read_text()
            value = json.loads(task.output_path.read_text())
            kw["validate_output"](task, value)
            outputs.append(value)
        return outputs
    monkeypatch.setattr(SlurmTaskBatch, "run", scheduler)
    run_hybrid_optimization(config)
    if persistent_output_link:
        from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
        assert staged.is_symlink()
        assert (persistent / PRIVATE_ARTIFACT_MARKER).is_file() is (not metadata_failure)
        assert (persistent / "gepa_state.bin").is_file()
        assert json.loads((persistent / "controller_status.json").read_text())["status"] == "completed"
    result = audit(path)
    assert result["structural_fidelity"] == "passed"
    assert result["physical_records"] == 6
    assert agent_calls == {"plan": 6, "code": 6}
    expected_phases = ({"reflector": 0, "curator": 0, "refiner": 0, "reflection": 1}
                       if learning_mode == "codex_reflection" else {"reflector": 2, "curator": 1, "refiner": 0})
    assert result["phases"] == expected_phases
    assert result["manual_review_required"] and 2 in waves
    if learning_mode == "codex_reflection":
        parent = next((config.run_dir / "hpc_tasks/hybrid_reflection").glob("*/attempts/task_0000/attempt_01/inputs/parent.json"))
        content = json.loads(parent.read_text())
        content["checklist"][0]["guide"] = "Lost evidence"
        parent.write_text(json.dumps(content))
        with pytest.raises(ValueError, match="staged parent"):
            audit(path)
        return
    curator = next((config.run_dir / "hpc_tasks/hybrid_curator").glob("*/attempts/task_0000/attempt_01/inputs/reflection_index.json"))
    content = json.loads(curator.read_text())
    content[0]["root_cause_analysis"] = "Lost evidence"
    curator.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="complete Ref"):
        audit(path)


def test_smoke_config_reuses_models_prompts_limits_and_budget():
    config = load_optimization_config("configs/gepa_safe_pce_verified_smoke_v1_20261022.yaml", require_api_keys=False)
    assert config.search.max_iterations == config.search.min_proposals == 1
    assert config.search.reflection_minibatch_size == 2
    assert config.search.max_metric_calls == 6
    assert (config.hybrid.max_items, config.hybrid.condition_tokens, config.hybrid.item_guide_tokens) == (10, 48, 96)
    assert config.hybrid.shared_guide_enabled is False
    assert all(getattr(config.hybrid, role)["reasoning_effort"] == "high"
               for role in ("reflector_model", "curator_model", "refiner_model"))
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    pce = load_swe_verified_pce_config("configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml", require_api_keys=False)
    assert pce.plan.thinking == "disabled" and pce.plan.temperature == 1.0
    assert pce.code.thinking is None  # Preserve the existing PCE default.
    assert pce.hpc.mem == config.hpc.mem == "4G"
    baseline = load_swe_verified_pce_config("configs/swe_verified_safe_pce_no_thinking_clean411_priority_ru7_iris_v1_20260919.yaml", require_api_keys=False)
    assert pce.code_prompt == baseline.code_prompt
    assert pce.code_instance_template == baseline.code_instance_template
    assert pce.plan_instance_template == baseline.plan_instance_template


@pytest.mark.parametrize("phase", ["plan", "code"])
def test_post_run_audit_accepts_terminal_agent_failure_without_invented_eval(tmp_path, phase):
    from scripts.tools.audit_safe_pce_ace_dataflow import _audit_worker_evidence
    from src.optimization.hpc.task_batch import atomic_json
    from src.optimization.online_models import ONLINE_OUTCOME_POLICY_VERSION
    plan = "# Exact original Plan\n"
    plan_trace = [{"role": "assistant", "content": "START_PLAN\n" + plan + "END_PLAN"}]
    failed_trace = [{"role": "user", "content": plan}, {"role": "assistant", "content": "invalid submission"}]
    output = dict(instance_id="case", fingerprint="fp", task_index=0, pce_run_index=1,
        pce_status="agent_failed", terminal_phase=phase, terminal_reason="invalid_submission",
        outcome_policy_version=ONLINE_OUTCOME_POLICY_VERSION, agent_failure_attempts=[1, 2, 3],
        last_worker_output={"failure_trajectory": failed_trace},
        evaluator_result={"status": "not_run", "reason": "agent_failed_before_evaluation"},
        plan=plan if phase == "code" else "", patch="",
        plan_trajectory=plan_trace if phase == "code" else failed_trace,
        code_trajectory=failed_trace if phase == "code" else [])
    batch = tmp_path / "hpc_tasks/pce/fp"
    atomic_json(batch / "outputs/task_0000.json", output)
    if phase == "code":
        atomic_json(batch / "checkpoints/task_0000/plan.json", {"payload": {
            "plan": plan, "trajectory": plan_trace,
            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
            "raw_plan_submission": "START_PLAN\n" + plan + "END_PLAN"}})
    _audit_worker_evidence(tmp_path, output)
    assert not (batch / "checkpoints/task_0000/evaluate.json").exists()
    output[phase + "_trajectory"] = [{"content": "silently changed"}]
    with pytest.raises(ValueError, match="worker -> physical"):
        _audit_worker_evidence(tmp_path, output)


def test_smoke_supervisor_and_generated_worker_scripts_match_reviewed_config(tmp_path):
    from scripts.hpc_supervisor_service import _load_launch_config
    from scripts.hpc_resume_loop import parse_args
    from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    from src.swe_verified_pce.hpc_executor import build_array_script
    launch = Path("configs/archive/supervisor_launches/safe_pce_ace_verified_smoke_supervisor_v1_20261022.yaml")
    _, _, arguments, _ = _load_launch_config(launch)
    # Adapt historical arguments without changing the frozen launch file.
    offset = arguments.index("--max-runs")
    arguments = arguments[:offset] + arguments[offset + 2:]
    supervisor = parse_args(arguments)  # No submit or remote probe.
    config = load_optimization_config(supervisor.runtime_config, require_api_keys=False)
    assert supervisor.ssh_target == "twang@access-iris.uni.lu"
    assert supervisor.slice_time == "00:08:00"
    assert not hasattr(supervisor, "max_runs")
    assert supervisor.require_clean_worktree and supervisor.reclaim_workspaces
    assert supervisor.remote_run_snapshot == (
        "/scratch/users/twang/vibe-coding-planning/run_state/" + str(config.run_dir.relative_to(Path.cwd())))
    assert supervisor.remote_staging_root.endswith("/staging/" + supervisor.job_name)
    phases = HybridPhaseExecutor(config)
    for role, indices in (("reflector", [0, 1]), ("curator", [0]), ("refiner", [0])):
        script = phases.script_for_wave(tmp_path / role, role, indices, 1)
        assert "#SBATCH --cpus-per-task=1" in script and "#SBATCH --mem=4G" in script
        assert "#SBATCH --time=00:30:00" in script
        assert "src.optimization.behavioral_hybrid_worker" in script
        assert config.hpc.worker_config_path in script
        assert "tools/Apptainer" in script and "lang/Python/3.11" in script
        assert "#SBATCH --array=" + ",".join(map(str, indices)) in script
    pce = load_swe_verified_pce_config("configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml", require_api_keys=False)
    script = build_array_script(config=pce, batch_dir=tmp_path / "pce", indices=[0, 1], attempt=1)
    assert "#SBATCH --time=01:00:00" in script
    assert "#SBATCH --cpus-per-task=1" in script and "#SBATCH --mem=4G" in script
    assert "src.swe_verified_pce.worker" in script and "tools/Apptainer" in script


@pytest.mark.parametrize("field", ["plan", "patch"])
def test_mismatching_evidence_hash_rejected(field):
    from src.optimization.safe_pce_learning import _validate_learning_evidence, IncompletePCEEvaluation
    from tests.test_optimization.test_safe_pce_learning import _output
    output = _output("task")
    output[field + "_sha256"] = "0" * 64
    with pytest.raises(IncompletePCEEvaluation, match="hash mismatch"):
        _validate_learning_evidence(output)
