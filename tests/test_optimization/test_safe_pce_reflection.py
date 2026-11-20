"""Reflection-only comparison: actual GEPA and shared Worker, no inference."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from src.exceptions import ControllerYield
from src.optimization import behavioral_hybrid_runtime as runtime
from src.optimization import behavioral_hybrid_worker as worker
from src.optimization import safe_pce_reflection as module
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_learning import SafePCEBackend
from tests.test_optimization.test_safe_pce_learning import _prepared, _output, ARTIFACT


def reflection_config(config):
    return replace(config, hybrid=replace(config.hybrid, learning_mode="codex_reflection",
        refiner_enabled=False, curator_model={"executor": "codex_cli", "model": "gpt-6-sol", "reasoning_effort": "high"},
        prompt_bundle="configs/prompts/safe_pce_reflection_v1.yaml", prompt_bundle_sha256=""))


def test_hpc_smoke_only_changes_reflection_and_fresh_run_identity():
    baseline = load_optimization_config("configs/gepa_safe_pce_rpg_codex_diag_smoke_v1_20261102.yaml", require_api_keys=False)
    smoke = load_optimization_config("configs/gepa_safe_pce_rpg_codex_reflection_smoke_v1_20261105.yaml", require_api_keys=False)
    for field in ("dataset_snapshot", "safe_pce_config", "initial_rules_path", "task", "search", "docker", "container", "execution", "reflection"):
        assert getattr(smoke, field) == getattr(baseline, field), field
    for field in baseline.hpc.__dataclass_fields__:
        if field not in {"remote_project_dir", "remote_task_dir", "job_name_prefix", "worker_config_path"}:
            assert getattr(smoke.hpc, field) == getattr(baseline.hpc, field), field
    for field in baseline.hybrid.__dataclass_fields__:
        if field not in {"learning_mode", "prompt_bundle", "prompt_bundle_sha256", "refiner_enabled", "reflector_model", "refiner_model"}:
            assert getattr(smoke.hybrid, field) == getattr(baseline.hybrid, field), field
    assert smoke.hybrid.learning_mode == "codex_reflection" and not smoke.hybrid.refiner_enabled
    assert smoke.hybrid.curator_model["model"] == "gpt-6-sol"
    assert smoke.hybrid.curator_model["reasoning_effort"] == "high"
    assert smoke.run_dir != baseline.run_dir
    assert smoke.search.max_iterations == 1 and smoke.search.reflection_minibatch_size == 2
    assert smoke.search.max_metric_calls == 6
    from scripts.hpc_supervisor_service import _load_launch_config
    session, _, args, script = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_rpg_codex_reflection_smoke_supervisor_v1_20261105.yaml"))
    assert session == smoke.run_dir.name
    assert args[args.index("--gepa-config") + 1] == "configs/gepa_safe_pce_rpg_codex_reflection_smoke_v1_20261105.yaml"
    assert script.name == "hpc_resume_loop.py"
    assert "--require-clean-worktree" in args and "--reclaim-workspaces" in args


def test_eight_proposal_binding_preserves_formal_outer_loop_and_smoke_prompt():
    formal = load_optimization_config("configs/gepa_safe_pce_rpg_codex_subagents_diag6_v1_20261104.yaml", require_api_keys=False)
    cfg = load_optimization_config("configs/gepa_safe_pce_rpg_codex_reflection_diag8_v1_20261105.yaml", require_api_keys=False)
    smoke = load_optimization_config("configs/gepa_safe_pce_rpg_codex_reflection_smoke_v1_20261105.yaml", require_api_keys=False)
    for field in ("dataset_snapshot", "safe_pce_config", "initial_rules_path", "task", "reflection", "docker", "container", "execution"):
        assert getattr(cfg, field) == getattr(formal, field), field
    for field in formal.hybrid.__dataclass_fields__:
        if field not in {"learning_mode", "prompt_bundle", "prompt_bundle_sha256", "refiner_enabled", "reflector_model", "refiner_model"}:
            assert getattr(cfg.hybrid, field) == getattr(formal.hybrid, field), field
    for field in formal.search.__dataclass_fields__:
        if field not in {"max_iterations", "min_proposals", "max_metric_calls", "projection_metric_calls"}:
            assert getattr(cfg.search, field) == getattr(formal.search, field), field
    for field in formal.hpc.__dataclass_fields__:
        if field not in {"remote_project_dir", "remote_task_dir", "job_name_prefix", "worker_config_path"}:
            assert getattr(cfg.hpc, field) == getattr(formal.hpc, field), field
    assert cfg.hybrid.prompt_bundle_sha256 == smoke.hybrid.prompt_bundle_sha256
    assert cfg.hybrid.learning_mode == "codex_reflection" and not cfg.hybrid.refiner_enabled
    assert cfg.search.max_iterations == cfg.search.min_proposals == 8
    assert cfg.search.max_metric_calls == cfg.search.projection_metric_calls == 15 + 8 * (8 + 8 + 15)
    assert len({cfg.run_dir, formal.run_dir, smoke.run_dir}) == 3
    from scripts.hpc_supervisor_service import _load_launch_config
    session, _, args, script = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_rpg_codex_reflection_diag8_supervisor_v1_20261105.yaml"))
    assert session == cfg.run_dir.name
    assert args[args.index("--gepa-config") + 1] == "configs/gepa_safe_pce_rpg_codex_reflection_diag8_v1_20261105.yaml"
    assert "--require-clean-worktree" in args and "--reclaim-workspaces" in args
    assert script.name == "hpc_resume_loop.py"


def test_twenty_proposal_binding_changes_only_budget_and_identity():
    old = load_optimization_config("configs/gepa_safe_pce_rpg_codex_reflection_diag8_v1_20261105.yaml", require_api_keys=False)
    path = "configs/gepa_safe_pce_rpg_codex_reflection_diag20_v1_20261106.yaml"
    cfg = load_optimization_config(path, require_api_keys=False)
    for field in cfg.__dataclass_fields__:
        if field not in {"run_dir", "search", "hpc"}:
            assert getattr(cfg, field) == getattr(old, field), field
    for field in old.search.__dataclass_fields__:
        if field not in {"max_iterations", "min_proposals", "max_metric_calls", "projection_metric_calls"}:
            assert getattr(cfg.search, field) == getattr(old.search, field), field
    for field in old.hpc.__dataclass_fields__:
        if field not in {"remote_project_dir", "remote_task_dir", "job_name_prefix", "worker_config_path"}:
            assert getattr(cfg.hpc, field) == getattr(old.hpc, field), field
    assert cfg.run_dir != old.run_dir
    assert cfg.search.max_iterations == cfg.search.min_proposals == 20
    assert cfg.search.max_metric_calls == cfg.search.projection_metric_calls == 15 + 20 * 31
    raw = yaml.safe_load(Path(path).read_text())
    contract = raw["experiment_contract"]
    assert contract["previous_output_import"] is False
    assert contract["budget"]["pce_evaluation_slots_without_cache_upper_bound"] == 635
    assert contract["budget"]["no_model_workspace_preparation_elements_without_retries_upper_bound"] == 1905
    assert contract["budget"]["reflection_units_upper_bound"] == 20
    from scripts.hpc_supervisor_service import _load_launch_config
    session, _, args, script = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_rpg_codex_reflection_diag20_supervisor_v1_20261106.yaml"))
    assert session == cfg.run_dir.name
    assert args[args.index("--gepa-config") + 1] == path
    assert {"--require-clean-worktree", "--reclaim-workspaces", "--reclaim-staging", "--submit"} <= set(args)
    assert script.name == "hpc_resume_loop.py"


def payload():
    rows = [{"instance_id": f"original-{i}", "split": "train", "task_semantics": "safe_pce_resolution_v1",
             "planner_input": {"task": "Original task"}, "score": 0.0,
             "retrospective_diagnostics": {"planning": "Original training-only reference"},
             "pce_runs": [{"pce_output": _output(f"original-{i}"), "score": 0.0}]} for i in range(2)]
    # Long Plans and opaque nested identity fields must survive staging.
    rows[0]["pce_runs"][0]["pce_output"]["plan"] = "unchanged plan\n" * 12000
    rows[0]["pce_runs"][0]["pce_output"]["unknown"] = {"id": "raw-id", "instance_id": "opaque"}
    return {"parent": deepcopy(ARTIFACT), "records": rows, "proposal_index": 0}


def test_config_only_requires_active_reflection_prompt_and_model(tmp_path, monkeypatch):
    raw = yaml.safe_load(Path("configs/gepa_safe_pce_rpg_codex_subagents_diag6_v1_20261104.yaml").read_text())
    raw["hybrid"].update(learning_mode="codex_reflection", refiner_enabled=False,
                          prompt_bundle="configs/prompts/safe_pce_reflection_v1.yaml")
    raw["hybrid"].pop("reflector_model")
    raw["hybrid"].pop("refiner_model")
    path = tmp_path / "reflection.yaml"
    path.write_text(yaml.safe_dump(raw))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = load_optimization_config(path)
    assert cfg.hybrid.learning_mode == "codex_reflection"
    for update, message in [({"refiner_enabled": True}, "refiner_enabled"),
                            ({"curator_model": {}}, "Native Codex")]:
        invalid = deepcopy(raw)
        invalid["hybrid"].update(update)
        path.write_text(yaml.safe_dump(invalid))
        with pytest.raises(ValueError, match=message):
            load_optimization_config(path, require_api_keys=False)


def test_single_call_has_complete_evidence_and_unmodified_full_candidate(tmp_path, monkeypatch):
    cfg = reflection_config(_prepared(tmp_path))
    data = payload()
    original = deepcopy(data)
    submitted = deepcopy(ARTIFACT)
    submitted["checklist"][0]["guide"] = "Inspect the actual contract before changing behavior."
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: tmp_path / "test.sif")
    calls = []

    class Conversation:
        def __init__(self, **kwargs):
            self.root = kwargs["container"].evidence_dir
            assert kwargs["container"].tool_network_disabled
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_json(self, **kwargs):
            calls.append(kwargs)
            manifest = json.loads((self.root / "manifest.json").read_text())
            assert not {"required_files", "optional_files"} & manifest.keys()
            assert all((self.root / entry["path"]).is_file() for entry in manifest["files"])
            assert json.loads((self.root / "parent.json").read_text()) == data["parent"]
            indexes = json.loads((self.root / "records.json").read_text())
            for row, entry in zip(data["records"], indexes, strict=True):
                assert {key: json.loads((self.root / path).read_text())
                        for key, path in entry["fields"].items()} == row
            assert "validation" not in manifest["entries"]
            assert not (self.root / "roles.json").exists()
            assert not (self.root / "ledger.json").exists()
            (self.root.parent / "candidate_response.json").write_text(json.dumps(submitted))
            return deepcopy(submitted), [{"role": "assistant", "content": "original response"}]
        def session_evidence(self): return []

    output, trajectory = module.run_reflection_phase(cfg, data, tmp_path / "attempt",
        retry_feedback="Previous output exceeded its limit.", conversation_factory=Conversation)
    assert len(calls) == 1
    assert "Previous output exceeded" in calls[0]["user"]
    assert output == submitted and data == original
    assert json.loads((tmp_path / "attempt/completion.json").read_text())["agent_output"] == submitted
    assert trajectory[0]["content"] == "original response"
    module.verify_reflection_evidence(tmp_path / "attempt", data, output, runtime.hybrid_limits(cfg))


@pytest.mark.parametrize("split", ["validation", "test"])
def test_reflection_never_stages_nontraining_data(tmp_path, split):
    data = payload()
    data["records"][0]["split"] = split
    with pytest.raises(ValueError, match="training evidence only"):
        module.write_reflection_evidence(tmp_path / "inputs", data, runtime.hybrid_limits(reflection_config(_prepared(tmp_path))))
    assert not (tmp_path / "inputs").exists()


def test_real_gepa_three_proposals_reuse_safe_pce_and_resume(tmp_path, monkeypatch):
    cfg = reflection_config(_prepared(tmp_path))
    backend = SafePCEBackend(cfg)
    train, val = backend.load_snapshot()
    train_ids, val_ids = {row.instance_id for row in train}, {row.instance_id for row in val}
    evaluations = []

    def execute(effective, mode, cases, **kwargs):
        evaluations.append([case.instance_id for case in cases])
        assert effective.plan_prompt == backend.pce.plan_prompt
        assert effective.code_prompt == backend.pce.code_prompt
        assert effective.code_instance_template == backend.pce.code_instance_template
        assert effective.plan == backend.pce.plan and effective.code == backend.pce.code
        assert effective.container == backend.pce.container and effective.hpc.mem == backend.pce.hpc.mem
        return [_output(case.instance_id, "revision" in effective.planning_rules) for case in cases]

    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)

    def forbidden(*args, **kwargs):
        pytest.fail("ACE machinery must not execute in complete-candidate reflection")
    monkeypatch.setattr(runtime, "IncrementalHybridACEProposer", forbidden)
    monkeypatch.setattr("src.optimization.ace_transaction.run_ace_transaction", forbidden)

    class Phases:
        calls = []
        def call(self, role, data):
            assert role == "reflection" and set(data) == {"parent", "records", "proposal_index"}
            assert data["proposal_index"] == len(self.calls)
            assert all(row["split"] == "train" and row["instance_id"] in train_ids - val_ids for row in data["records"])
            self.calls.append(deepcopy(data))
            candidate = deepcopy(data["parent"])
            candidate["checklist"][0]["guide"] = f"Inspect revision {len(self.calls)} against required behavior."
            return candidate, [{"role": "assistant", "content": "full replacement"}]

    phases = Phases()
    runtime.run_hybrid_optimization(cfg, phase_executor=phases, evaluation_backend=backend)
    assert len(phases.calls) == 3
    assert not (cfg.run_dir / "global_counter_ledger.json").exists()
    assert len(list((cfg.run_dir / "hybrid_proposals").glob("*.json"))) == 3
    before = len(evaluations)
    runtime.run_hybrid_optimization(cfg, phase_executor=phases, evaluation_backend=SafePCEBackend(cfg))
    assert len(phases.calls) == 3 and len(evaluations) == before


def test_shared_worker_retries_invalid_candidate_with_specific_feedback(tmp_path, monkeypatch):
    cfg = reflection_config(_prepared(tmp_path))
    data = payload()
    manifest = {"mode": "behavioral_hybrid_phase", "role": "reflection", "payload": data,
                "fingerprint": runtime.phase_identity(cfg, "reflection", data)}
    path, output = tmp_path / "task.json", tmp_path / "output.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(worker, "load_optimization_config", lambda *_: cfg)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: tmp_path / "test.sif")
    outputs = [{"schema_version": 1, "checklist": []}, deepcopy(ARTIFACT)]
    seen = []
    class Conversation:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_json(self, **kwargs):
            seen.append(kwargs["user"])
            return outputs.pop(0), []
        def session_evidence(self): return []
    original = module.run_reflection_phase
    monkeypatch.setattr(module, "run_reflection_phase", lambda *args, **kwargs:
        original(*args, **kwargs, conversation_factory=Conversation))
    args = dict(config_path=Path("unused"), manifest_path=path, output_path=output)
    assert worker.run_task(**args, attempt_dir=tmp_path / "attempt1") == 1
    failure = json.loads(output.read_text())
    assert failure["failure_stage"] == "agent_output_validation"
    assert failure["raw_agent_output"]["checklist"] == []
    previous = tmp_path / "previous.json"
    output.rename(previous)
    assert worker.run_task(**args, attempt_dir=tmp_path / "attempt2", previous_output_path=previous) == 0
    assert "checklist must contain" in seen[1]
    assert json.loads(output.read_text())["agent_output"] == ARTIFACT


def test_existing_slurm_wave_pins_and_reuses_completed_reflection(tmp_path, monkeypatch):
    cfg = reflection_config(_prepared(tmp_path))
    cfg = replace(cfg, execution=replace(cfg.execution, backend="hpc_slurm"))
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: tmp_path / "test.sif")
    executor = runtime.HybridPhaseExecutor(cfg)
    submissions = []
    executor.runtime.submitter = lambda path: submissions.append(path.read_text()) or "12345"
    data = payload()
    with pytest.raises(ControllerYield):
        executor.call("reflection", data)
    assert len(submissions) == 1 and "--array=0" in submissions[0]
    batch = cfg.run_dir / "hpc_tasks/hybrid_reflection/proposal-0000"
    manifest = json.loads((batch / "tasks/task_0000.json").read_text())
    result = batch / "outputs/task_0000.json"
    result.parent.mkdir(exist_ok=True)
    result.write_text(json.dumps({"status": "completed", "fingerprint": manifest["fingerprint"],
                                 "role": "reflection", "agent_output": ARTIFACT, "trajectory": []}))
    assert executor.call("reflection", data) == (ARTIFACT, [])
    assert len(submissions) == 1
    data["parent"]["checklist"][0]["guide"] = "Different request"
    with pytest.raises(ValueError, match="request/source changed"):
        executor.call("reflection", data)
