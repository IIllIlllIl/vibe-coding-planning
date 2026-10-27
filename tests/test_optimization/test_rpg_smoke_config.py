"""RPG smoke differs from completed Verified v5 only at dataset bindings."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import load_pce_config, pce_cases


BASELINE = Path("configs/gepa_safe_pce_verified_codex_smoke_v5_20261024.yaml")
LEARNING = Path("configs/gepa_safe_pce_rpg_codex_smoke_v1_20261024.yaml")
PCE = Path("configs/safe_pce_ace_rpg_codex_smoke_source_v1_20261024.yaml")
SUPERVISOR = Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v1_20261024.yaml")
FROZEN = Path("configs/frozen_safe_pce_ace/rpg_smoke_v1_20261024")


def test_rpg_smoke_preserves_verified_learning_and_agent_policies():
    old = load_optimization_config(BASELINE, require_api_keys=False)
    new = load_optimization_config(LEARNING, require_api_keys=False)
    assert new.search == old.search
    assert new.reflection == old.reflection
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.initial_rules_path == old.initial_rules_path
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts",
                  "poll_interval_seconds", "max_running_array_tasks"):
        assert getattr(new.hpc, field) == getattr(old.hpc, field)
    baseline = yaml.safe_load(Path("configs/safe_pce_ace_verified_codex_smoke_source_v5_20261024.yaml").read_text())
    current = yaml.safe_load(PCE.read_text())
    for field in ("plan", "code", "docker", "container", "evaluator"):
        assert current[field] == baseline[field]
    for field in ("prompt_source_config", "plan_prompt_source_config", "code_prompt_source_config"):
        assert current["paths"][field] == baseline["paths"][field]
    for field in ("job_name_prefix", "worker_config_path"):
        baseline["hpc"].pop(field)
        current["hpc"].pop(field)
    assert current["hpc"] == baseline["hpc"]


def test_rpg_smoke_packages_exact_source_and_official_evaluator_without_references():
    new = load_optimization_config(LEARNING, require_api_keys=False)
    verify_hybrid_snapshot(new)
    mode, pce = load_pce_config(new.safe_pce_config)
    assert mode == "swe_rpg_pce"
    cases = pce_cases(mode, pce)
    assert {case.working_directory for case in cases} == {"/testbed", "/testbed2"}
    split = json.loads((FROZEN / "split-selection.json").read_text())
    assert {case.instance_id for case in cases} == set(split["train"] + split["validation"])
    assert (len(split["train"]), len(split["validation"])) == (2, 1)
    assert not set(split["train"]) & set(split["validation"])
    assert {case.working_directory for case in cases if case.instance_id in split["train"]} == {"/testbed", "/testbed2"}
    source = Path("output/SWE-RPG/pce-inputs/python78-v1-20261024")
    for name in ("manifest.json", "instances.jsonl", "evaluation/evaluate_docker.py"):
        assert (pce.dataset_snapshot / name).read_bytes() == (source / name).read_bytes()
    assert not list(pce.dataset_snapshot.rglob("*mid*groundtruth*"))
    for case in cases:
        visible = case.agent_projection()
        assert visible["repository"]["working_directory"] == case.working_directory
        assert not {"patch", "test_patch", "log_parser", "test_cmds", "FAIL_TO_PASS", "PASS_TO_PASS", "PR_Body"} & visible.keys()
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == hashlib.sha256(PCE.read_bytes()).hexdigest()
    assert manifest["engineering_baseline_config_sha256"] == hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    assert pce.run_dir == new.run_dir / "pce_evaluations"
    assert all(role["model"] == "gpt-6-sol" and role["reasoning_effort"] == "high" for role in pce.agent_backends.values())
    assert pce.agent_network_disabled is True


def test_rpg_smoke_reuses_two_reflector_wave_and_standard_supervisor(tmp_path):
    from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
    new = load_optimization_config(LEARNING, require_api_keys=False)
    script = HybridPhaseExecutor(new).script_for_wave(tmp_path / "batch", "reflector", [0, 1], 1)
    assert "#SBATCH --array=0,1\n" in script
    assert "src.optimization.behavioral_hybrid_worker" in script
    session, log, args, program = _load_launch_config(SUPERVISOR)
    assert program == RESUME_SCRIPT and session in log
    assert args[args.index("--gepa-config") + 1] == str(LEARNING)
    assert args[args.index("--slice-time") + 1] == "00:08:00"
    assert args[args.index("--max-runs") + 1] == "24"
    for flag in ("--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces", "--submit"):
        assert flag in args
    assert not {"--remote-dir", "--remote-dataset-dir", "--remote-run-dir"} & set(args)
    raw = yaml.safe_load(LEARNING.read_text())
    assert raw["experiment_contract"]["previous_output_import"] is False
    assert raw["experiment_contract"]["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}


def test_rpg_python_recovery_keeps_science_and_uses_fresh_runtime(tmp_path):
    from scripts.hpc_supervisor_service import _load_launch_config
    from src.optimization.safe_pce_execution import materialize_pce_config
    recovery_path = Path("configs/gepa_safe_pce_rpg_codex_smoke_v2_20261025.yaml")
    old = load_optimization_config(LEARNING, require_api_keys=False)
    new = load_optimization_config(recovery_path, require_api_keys=False)
    assert old.search == new.search and old.reflection == new.reflection
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert old.run_dir != new.run_dir and old.dataset_snapshot != new.dataset_snapshot
    verify_hybrid_snapshot(new)
    for name in ("train.jsonl", "validation.jsonl", "pce_source/instances.jsonl", "pce_source/images.json"):
        assert (old.dataset_snapshot / name).read_bytes() == (new.dataset_snapshot / name).read_bytes()
    mode, current = load_pce_config(new.safe_pce_config)
    _, previous = load_pce_config(old.safe_pce_config)
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker", "agent_network_disabled"):
        assert getattr(previous, field) == getattr(current, field)
    assert mode == "swe_rpg_pce" and current.run_dir == new.run_dir / "pce_evaluations"
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts", "max_running_array_tasks"):
        assert getattr(current.hpc, field) == getattr(previous.hpc, field)
    raw = yaml.safe_load(recovery_path.read_text())
    assert raw["experiment_contract"]["previous_output_import"] is False
    assert raw["experiment_contract"]["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
    session, _, args, _ = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v2_20261025.yaml"))
    assert session == new.run_dir.name
    assert args[args.index("--gepa-config") + 1] == str(recovery_path)
    artifact = json.loads(Path("configs/prompts/safe_pce_hybrid_seed_v1.json").read_text())
    effective = materialize_pce_config(current, artifact, tmp_path, run_dir=new.run_dir)
    assert effective.run_dir == new.run_dir


def test_rpg_smoke_v3_reuses_completed_graph_with_reviewed_prompts_and_placeholder(tmp_path):
    from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
    from src.optimization.safe_pce_learning import SafePCEBackend
    from src.optimization.safe_pce_execution import sha256

    path = Path("configs/gepa_safe_pce_rpg_codex_smoke_v3_20261027.yaml")
    old = load_optimization_config("configs/gepa_safe_pce_rpg_codex_smoke_v2_20261025.yaml", require_api_keys=False)
    new = load_optimization_config(path, require_api_keys=False)
    assert new.search == old.search and new.reflection == old.reflection
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.initial_rules_path == Path("configs/prompts/safe_pce_hybrid_seed_v2.json").resolve()
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    backend = SafePCEBackend(replace(new, execution=replace(new.execution, backend="local")))
    train, validation = backend.load_snapshot()
    split = json.loads((FROZEN / "split-selection.json").read_text())
    assert [case.instance_id for case in train] == split["train"]
    assert [case.instance_id for case in validation] == split["validation"]
    expected = set(split["train"] + split["validation"])
    formal = json.loads(Path("configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json").read_text())
    assert expected <= set(formal["train"])
    rows = (backend.pce.dataset_snapshot / "instances.jsonl").read_bytes().splitlines(keepends=True)
    assert len(rows) == 3 and {json.loads(row)["instance_id"] for row in rows} == expected
    source = {json.loads(row)["instance_id"]: row for row in Path(
        "output/SWE-RPG/pce-inputs/python78-v1-20261024/instances.jsonl").read_bytes().splitlines(keepends=True)}
    assert all(row == source[json.loads(row)["instance_id"]] for row in rows)
    assert backend.learning_diagnostics == {}
    assert not (new.dataset_snapshot / "learning_diagnostics").exists()
    planner = Path("configs/prompts/safe_pce_rules_planner_v2.yaml")
    coder = Path("configs/prompts/safe_pce_native_codex_code_v2.yaml")
    assert backend.pce.plan_prompt == yaml.safe_load(planner.read_text())["prompts"]["plan_system"]
    assert backend.pce.code_prompt == yaml.safe_load(coder.read_text())["prompts"]["code_system"]
    mode, previous = load_pce_config(old.safe_pce_config)
    for field in ("plan", "code", "agent_backends", "container", "docker", "agent_network_disabled"):
        assert getattr(backend.pce, field) == getattr(previous, field)
    for field in ("cpus_per_task", "mem", "time", "max_task_attempts"):
        assert getattr(backend.pce.hpc, field) == getattr(previous.hpc, field)
    assert backend.pce.hpc.max_running_array_tasks == 0
    assert new.hpc.max_running_array_tasks == 0
    from src.swe_verified_pce.hpc_executor import build_array_script
    pce_script = build_array_script(config=backend.pce, batch_dir=tmp_path / "pce",
                                    indices=[0, 1, 2, 3], attempt=1)
    assert "#SBATCH --array=0,1,2,3\n" in pce_script
    assert "max_running_array_tasks" not in yaml.safe_load(Path(
        "configs/safe_pce_ace_rpg_codex_smoke_source_v3_20261027.yaml").read_text())["hpc"]
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(Path(
        "configs/safe_pce_ace_rpg_codex_smoke_source_v3_20261027.yaml"))
    assert manifest["planner_prompt_source"]["sha256"] == sha256(planner)
    assert manifest["initial_guideline_source"]["sha256"] == sha256(new.initial_rules_path)
    launch = Path("configs/safe_pce_ace_rpg_codex_smoke_supervisor_v3_20261027.yaml")
    session, log, args, program = _load_launch_config(launch)
    assert program == RESUME_SCRIPT and session == new.run_dir.name and session in log
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
    contract = yaml.safe_load(path.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is False
    assert contract["previous_output_import"] is False
    assert contract["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
