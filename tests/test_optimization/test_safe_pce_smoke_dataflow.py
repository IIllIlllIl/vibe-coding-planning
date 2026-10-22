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


def test_verified_end_to_end_and_audit_detects_lost_evidence(tmp_path, monkeypatch):
    from src.optimization import behavioral_hybrid_runtime as runtime
    from src.optimization.hpc.task_batch import SlurmTaskBatch
    from src.optimization.behavioral_hybrid_worker import run_task as phase_worker
    from src.swe_verified_pce.worker import run_task as pce_worker
    from src.swe_verified_pce.runner import SWEVerifiedPCERunner
    path = _prepared_verified(tmp_path)
    config = load_optimization_config(path, require_api_keys=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda config: None)
    monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate", lambda *a, **kw: (tmp_path / "bundle", {}))
    monkeypatch.setattr("src.swe_verified_pce.worker.configure_docker_capacity", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(SWEVerifiedPCERunner, "_verify_sif", lambda *a: None)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_ensure_repository_history", lambda *a: (tmp_path / "bundle", {}))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_restore_agent_repository", lambda *a, **kw: None)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_environment", lambda *a, **kw: SimpleNamespace(cleanup=lambda: None))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_capture_code_workspace_evidence", lambda *a, **kw: {})
    plan = " \n# Plan\n\n```python\nx = 1\n```\n" + ("Evidence.\n" * 4000) + "Rule 1: inspect requirements.\nDecisive tail.\n\n"
    def planner(agent_config, issue, env, **kw):
        assert "plan-00001" not in kw["planning_rules"]
        return plan, [{"role": "user", "content": issue + "\n" + kw["planning_rules"]},
                      {"role": "assistant", "content": "START_PLAN\n" + plan + "END_PLAN"}]
    def coder(agent_config, received, issue, env, **kw):
        assert received == plan
        assert "planning_rules" not in kw
        return _diff("src/module.py"), [{"role": "user", "content": "Issue\n" + received}]
    monkeypatch.setattr("src.swe_verified_pce.runner.plan_agent.run", planner)
    monkeypatch.setattr("src.swe_verified_pce.runner.code_agent.run", coder)
    original = SWEVerifiedPCERunner.run
    def run(self, case):
        resolved = "revision" in self.config.planning_rules
        self.evaluator = lambda *a, **kw: {"status": "completed",
            "terminal_kind": "official_tests_resolved" if resolved else "official_tests_unresolved",
            "evaluator_resolved": resolved, "task_outcome": "resolved" if resolved else "unresolved",
            "official_report": {case.instance_id: {"resolved": resolved, "tests_status": {"FAIL_TO_PASS": {"success": ["x"]}}}}}
        return original(self, case)
    monkeypatch.setattr(SWEVerifiedPCERunner, "run", run)
    def agent(**kw):
        root = Path(kw["evidence_dir"])
        role = json.loads((root / "manifest.json").read_text())["role"]
        parent = json.loads((root / "counted_playbook.json").read_text())
        if role == "reflector":
            record = json.loads((root / "record.json").read_text())
            return review(parent, record), []
        assert role == "curator"
        records = json.loads((root / "records.json").read_text())
        return {"reasoning": "Evidence-supported revision", "operations": [{"type": "UPDATE",
            "target_id": parent["checklist"][0]["id"], "content": {
                "condition": parent["checklist"][0]["condition"], "guide": "Compare the requirements; revision."},
            "supporting_instance_ids": [records[0]["instance_id"]]}]}, []
    monkeypatch.setattr(runtime, "run_evidence_reflector", agent)
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
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
            if manifest["mode"] == "swe_verified_pce":
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
    result = audit(path)
    assert result["structural_fidelity"] == "passed"
    assert result["physical_records"] == 6
    assert result["phases"] == {"reflector": 2, "curator": 1, "refiner": 0}
    assert result["manual_review_required"] and 2 in waves
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


def test_smoke_supervisor_and_generated_worker_scripts_match_reviewed_config(tmp_path):
    from scripts.hpc_supervisor_service import _load_launch_config
    from scripts.hpc_resume_loop import parse_args
    from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    from src.swe_verified_pce.hpc_executor import build_array_script
    launch = Path("configs/safe_pce_ace_verified_smoke_supervisor_v1_20261022.yaml")
    _, _, arguments, _ = _load_launch_config(launch)
    supervisor = parse_args(arguments)  # Parsing only: no submit or remote probe.
    config = load_optimization_config(supervisor.runtime_config, require_api_keys=False)
    assert supervisor.ssh_target == "twang@access-iris.uni.lu"
    assert supervisor.slice_time == "00:08:00" and supervisor.max_runs == 24
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
