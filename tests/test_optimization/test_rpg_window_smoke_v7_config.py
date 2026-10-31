"""Small real PCE/ACE binding exercises the window boundary, not window-8 size."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import RESUME_SCRIPT, _load_launch_config
from scripts.hpc_resume_loop import parse_args
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_smoke_v7_20261029.yaml")
SOURCE = Path("configs/safe_pce_ace_rpg_codex_smoke_source_v7_20261029.yaml")
SUPERVISOR = Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v7_20261029.yaml")
BASELINE = Path("configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml")


def test_post_layout_smoke_preserves_completed_baseline_scientific_inputs():
    new = load_optimization_config(
        "configs/gepa_safe_pce_rpg_codex_smoke_v8_20261031.yaml", require_api_keys=False)
    old = load_optimization_config(CONFIG, require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.search == old.search and new.task == old.task
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.initial_rules_path == old.initial_rules_path
    assert new.run_dir != old.run_dir and not new.run_dir.exists()
    a, b = local(new), local(old)
    train, validation = a.load_snapshot()
    assert a.learning_diagnostics == {} and a.pce.pce_window_size == 1
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker",
                  "agent_network_disabled", "workspace_preparation", "slurm_evaluator_timeout_outcome"):
        assert getattr(a.pce, field) == getattr(b.pce, field)
    for name in ("train.jsonl", "validation.jsonl"):
        assert (new.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    for name in ("manifest.json", "instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (a.pce.dataset_snapshot / name).read_bytes() == (b.pce.dataset_snapshot / name).read_bytes()
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(
        Path("configs/safe_pce_ace_rpg_codex_smoke_source_v8_20261031.yaml"))
    assert manifest["engineering_baseline_config_sha256"] == sha256(CONFIG)
    assert [c.instance_id for c in train] == ["falconry__falcon-2540", "openai__openai-agents-python-454"]
    assert [c.instance_id for c in validation] == ["pylint-dev__pylint-10797"]


def test_post_layout_launch_uses_shared_supervisor_with_fresh_identity(tmp_path):
    path = Path("configs/gepa_safe_pce_rpg_codex_smoke_v8_20261031.yaml")
    config = load_optimization_config(path, require_api_keys=False)
    session, log, args, program = _load_launch_config(
        Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v8_20261031.yaml"))
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == "00:08:00" and launch.max_runs == 60
    assert args[args.index("--gepa-config") + 1] == str(path)
    raw = yaml.safe_load(path.read_text())["experiment_contract"]
    assert raw["development_only"] and not raw["previous_output_import"]
    assert raw["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
    effective = materialize_pce_config(local(config).pce,
        json.loads(config.initial_rules_path.read_text()), tmp_path / "effective", run_dir=config.run_dir)
    for prepare, count in ((True, 3), (False, 1)):
        script = build_array_script(config=effective, batch_dir=tmp_path,
            indices=list(range(count)), attempt=1, preparation_only=prepare)
        assert "#SBATCH --array=" + ",".join(map(str, range(count))) + "\n" in script
        assert "src.swe_verified_pce.worker" in script
        assert "#SBATCH --mem=4G" in script and "#SBATCH --time=01:00:00" in script
        if prepare:
            assert "ENV_FILE" not in script and "source " not in script


def local(config):
    return SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))


def test_smoke_only_changes_execution_window_and_fresh_identity():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    old = load_optimization_config(BASELINE, require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.search == old.search and new.task == old.task
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.initial_rules_path == old.initial_rules_path
    assert new.run_dir != old.run_dir and not new.run_dir.exists()
    a, b = local(new), local(old)
    assert a.pce.pce_window_size == 1 and b.pce.pce_window_size == 0
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker",
                  "agent_network_disabled", "workspace_preparation", "slurm_evaluator_timeout_outcome"):
        assert getattr(a.pce, field) == getattr(b.pce, field)
    for field in ("partition", "cpus_per_task", "mem", "time", "max_running_array_tasks", "max_task_attempts"):
        assert getattr(a.pce.hpc, field) == getattr(b.pce.hpc, field)
    contract = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is False and contract["development_only"] is True
    assert contract["previous_output_import"] is False
    assert contract["pce_execution_window_cases"] == 1 and contract["formal_execution_window_cases"] == 8
    assert contract["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
    assert contract["budget"]["reflector_calls"] == 2 and contract["budget"]["curator_calls"] == 1
    formal = load_optimization_config("configs/gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml", require_api_keys=False)
    assert formal.search.reflection_minibatch_size == 8 and local(formal).pce.pce_window_size == 8


def test_smoke_retains_exact_three_development_cases_and_no_diagnostics():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    old = load_optimization_config(BASELINE, require_api_keys=False)
    a, b = local(new), local(old)
    train, validation = a.load_snapshot()
    assert [case.instance_id for case in train] == ["falconry__falcon-2540", "openai__openai-agents-python-454"]
    assert [case.instance_id for case in validation] == ["pylint-dev__pylint-10797"]
    assert a.learning_diagnostics == {}
    assert {case.source.working_directory for case in train + validation} == {"/testbed", "/testbed2"}
    for name in ("train.jsonl", "validation.jsonl"):
        assert (new.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    for name in ("manifest.json", "instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (a.pce.dataset_snapshot / name).read_bytes() == (b.pce.dataset_snapshot / name).read_bytes()
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(SOURCE)
    assert manifest["engineering_baseline_config_sha256"] == sha256(BASELINE)


def test_smoke_reuses_shared_supervisor_and_actual_preparation_worker(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    session, log, args, program = _load_launch_config(SUPERVISOR)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == "00:08:00" and launch.max_runs == 60
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    backend = local(config)
    effective = materialize_pce_config(backend.pce, json.loads(config.initial_rules_path.read_text()),
                                     tmp_path / "effective", run_dir=config.run_dir)
    assert effective.pce_window_size == 1
    for prepare, count in ((True, 3), (False, 1)):
        script = build_array_script(config=effective, batch_dir=tmp_path,
            indices=list(range(count)), attempt=1, preparation_only=prepare)
        assert "#SBATCH --array=" + ",".join(map(str, range(count))) + "\n" in script
        assert "src.swe_verified_pce.worker" in script
        assert "#SBATCH --mem=4G" in script and "#SBATCH --time=01:00:00" in script
        if prepare:
            assert "ENV_FILE" not in script and "source " not in script
