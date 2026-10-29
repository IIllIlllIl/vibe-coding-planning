"""Mini-8 learning and an independent eight-case PCE storage window."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import RESUME_SCRIPT, _load_launch_config
from scripts.hpc_resume_loop import parse_args
from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml")
SOURCE = Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v9_20261029.yaml")
SUPERVISOR = Path("configs/safe_pce_ace_rpg_codex_nodiag3_supervisor_v9_20261029.yaml")
BASELINE = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v8_20261028.yaml")
SMOKE = Path("configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml")


def backend(config):
    return SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))


def test_mini8_and_window8_preserve_prompts_models_and_experimental_contract():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    old = load_optimization_config(BASELINE, require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.search == replace(old.search, reflection_minibatch_size=8,
                                max_metric_calls=108, projection_metric_calls=108)
    assert new.task == old.task and new.initial_rules_path == old.initial_rules_path
    a, b = backend(new), backend(old)
    assert a.pce.pce_window_size == 8 and b.pce.pce_window_size == 0
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker",
                  "agent_network_disabled", "workspace_preparation", "slurm_evaluator_timeout_outcome"):
        assert getattr(a.pce, field) == getattr(b.pce, field)
    for role in ("reflector_model", "curator_model", "refiner_model"):
        model = getattr(new.hybrid, role)
        assert model["model"] == "gpt-6-sol" and model["reasoning_effort"] == "high"
    raw = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert raw["launch_authorized"] is False and raw["previous_output_import"] is False
    assert raw["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    assert raw["pce_execution_window_cases"] == 8 and raw["maximum_prepared_phase_trees_per_window"] == 24
    assert raw["budget"]["pce_evaluation_slots_without_cache_upper_bound"] == 15 + 3 * (16 + 15)
    assert raw["budget"]["no_model_workspace_preparation_elements_without_retries_upper_bound"] == 324
    assert not new.run_dir.exists() and new.run_dir != old.run_dir


def test_frozen_input_bytes_and_no_diagnostics_or_downstream_leakage():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    old = load_optimization_config(BASELINE, require_api_keys=False)
    a, b = backend(new), backend(old)
    train, validation = a.load_snapshot()
    assert (len(train), len(validation)) == (46, 15) and a.learning_diagnostics == {}
    for name in ("train.jsonl", "validation.jsonl"):
        assert (new.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    for name in ("manifest.json", "instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (a.pce.dataset_snapshot / name).read_bytes() == (b.pce.dataset_snapshot / name).read_bytes()
    assert not (new.dataset_snapshot / "learning_diagnostics").exists()
    assert not list(new.dataset_snapshot.rglob("*downstream*"))
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(SOURCE)
    assert manifest["engineering_baseline_config_sha256"] == sha256(SMOKE)


def test_supervisor_and_effective_config_reuse_existing_unthrottled_workers(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    session, log, args, program = _load_launch_config(SUPERVISOR)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == "00:08:00" and launch.max_runs == 60
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert launch.remote_staging_root.endswith("/staging/" + session)
    a = backend(config)
    effective = materialize_pce_config(a.pce, json.loads(config.initial_rules_path.read_text()),
        tmp_path / "effective", run_dir=config.run_dir)
    assert effective.pce_window_size == 8
    for prepare, count in ((True, 24), (False, 8)):
        script = build_array_script(config=effective, batch_dir=tmp_path,
            indices=list(range(count)), attempt=1, preparation_only=prepare)
        assert "#SBATCH --array=" + ",".join(map(str, range(count))) + "\n" in script
        assert "#SBATCH --time=01:00:00" in script and "#SBATCH --mem=4G" in script
        assert "src.swe_verified_pce.worker" in script
        if prepare:
            assert "ENV_FILE" not in script and "source " not in script
    phases = HybridPhaseExecutor(config)
    script = phases.script_for_wave(tmp_path / "ref", "reflector", list(range(8)), 1)
    assert "#SBATCH --array=0,1,2,3,4,5,6,7\n" in script
