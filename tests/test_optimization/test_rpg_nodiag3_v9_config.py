"""Mini-8 learning and an independent eight-case PCE storage window."""
from dataclasses import replace
import json
from pathlib import Path

import yaml
import pytest

from scripts.hpc_supervisor_service import RESUME_SCRIPT, _load_launch_config
from scripts.hpc_resume_loop import parse_args
from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml")
SOURCE = Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v9_20261029.yaml")
SUPERVISOR = Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v9_20261029.yaml")
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


@pytest.mark.parametrize("config_path,supervisor_path", [
    (CONFIG, SUPERVISOR),
    (Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v10_20261101.yaml"),
     Path("configs/safe_pce_ace_rpg_codex_nodiag3_supervisor_v10_20261101.yaml")),
])
def test_supervisor_and_effective_config_reuse_existing_unthrottled_workers(tmp_path, config_path, supervisor_path):
    config = load_optimization_config(config_path, require_api_keys=False)
    session, log, args, program = _load_launch_config(supervisor_path)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == "00:08:00" and not hasattr(launch, "max_runs")
    assert "--max-runs" not in args and "--recover-controller-error-type-once" not in args
    assert args[args.index("--gepa-config") + 1] == str(config_path)
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


def test_v10_fresh_formal_binding_preserves_v9_science_and_completed_smoke_runtime():
    path = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v10_20261101.yaml")
    new = load_optimization_config(path, require_api_keys=False)
    old = load_optimization_config(CONFIG, require_api_keys=False)
    smoke_path = Path("configs/gepa_safe_pce_rpg_codex_smoke_v10_20261101.yaml")
    smoke = load_optimization_config(smoke_path, require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.search == old.search and new.reflection == old.reflection
    assert new.task == old.task and new.initial_rules_path == old.initial_rules_path
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.hybrid == replace(smoke.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    a, b, completed = backend(new), backend(old), backend(smoke)
    train, val = a.load_snapshot()
    assert (len(train), len(val)) == (46, 15) and a.learning_diagnostics == {}
    assert a.pce.pce_window_size == 8 and completed.pce.pce_window_size == 1
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker",
                  "agent_network_disabled", "workspace_preparation", "slurm_evaluator_timeout_outcome"):
        assert getattr(a.pce, field) == getattr(b.pce, field) == getattr(completed.pce, field)
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts",
                  "max_running_array_tasks"):
        assert getattr(a.pce.hpc, field) == getattr(completed.pce.hpc, field)
        assert getattr(new.hpc, field) == getattr(smoke.hpc, field)
    assert a.pce.run_dir == new.run_dir / "pce_evaluations"
    assert a.pce.hpc.worker_config_path == str(new.safe_pce_config.relative_to(Path.cwd()))
    assert new.hpc.worker_config_path == str(path)
    for name in ("train.jsonl", "validation.jsonl", "pce_source/manifest.json",
                 "pce_source/instances.jsonl", "pce_source/images.json",
                 "pce_source/evaluation/evaluate_docker.py"):
        assert (new.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    assert not (new.dataset_snapshot / "learning_diagnostics").exists()
    assert not list(new.dataset_snapshot.rglob("*downstream*"))
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(Path(
        "configs/safe_pce_ace_rpg_codex_nodiag3_source_v10_20261101.yaml"))
    assert manifest["engineering_baseline_config_sha256"] == sha256(smoke_path)
    raw = yaml.safe_load(path.read_text())["experiment_contract"]
    assert raw["launch_authorized"] is True and raw["previous_output_import"] is False
    assert raw["diagnostics_visibility"] == "none"
    assert raw["matched_diagnostic_counterpart"] == "not_prepared_or_authorized"
    assert raw["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    assert raw["budget"]["pce_evaluation_slots_without_cache_upper_bound"] == 108
    assert raw["budget"]["no_model_workspace_preparation_elements_without_retries_upper_bound"] == 324
    assert raw["budget"]["reflector_calls_upper_bound"] == 24
    assert raw["budget"]["curator_calls_upper_bound"] == 3


def test_patch_smoke_v11_preserves_learning_contract_and_exercises_split_submission(tmp_path):
    path = Path("configs/gepa_safe_pce_rpg_codex_smoke_v11_20261101.yaml")
    new = load_optimization_config(path, require_api_keys=False)
    old = load_optimization_config(Path("configs/gepa_safe_pce_rpg_codex_smoke_v10_20261101.yaml"),
                                   require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.search == old.search and new.reflection == old.reflection
    assert new.task == old.task and new.initial_rules_path == old.initial_rules_path
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    a, b = backend(new), backend(old)
    train, val = a.load_snapshot()
    selection = json.loads(Path("configs/frozen_safe_pce_ace/rpg_patch_smoke_v11_20261101/split-selection.json").read_text())
    formal = json.loads(Path("configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json").read_text())
    assert (len(train), len(val)) == (2, 1) and a.learning_diagnostics == {}
    assert selection["validation"] == ["pdm-project__pdm-2795"]
    for split in ("train", "validation"):
        actual = [json.loads(line)["instance_id"] for line in (new.dataset_snapshot / (split + ".jsonl")).read_text().splitlines()]
        assert actual == selection[split] and set(actual) <= set(formal[split])
    assert a.pce.code_submission_protocol == "split_code_test_v1"
    assert b.pce.code_submission_protocol == "staged_diff_v1"
    prompts = yaml.safe_load(Path("configs/prompts/safe_pce_code_split_v1.yaml").read_text())["prompts"]
    assert a.pce.code_prompt == prompts["code_system"]
    assert a.pce.code_instance_template == prompts["code_instance"]
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "container", "docker", "agent_network_disabled", "workspace_preparation",
                  "slurm_evaluator_timeout_outcome"):
        assert getattr(a.pce, field) == getattr(b.pce, field)
    assert a.pce.hpc.max_task_attempts == 3 and new.hpc.max_task_attempts == 2
    assert a.pce.pce_window_size == b.pce.pce_window_size == 1
    source = Path("configs/safe_pce_ace_rpg_codex_smoke_source_v11_20261101.yaml")
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(source)
    assert manifest["engineering_baseline_config_sha256"] == sha256(Path("configs/gepa_safe_pce_rpg_codex_smoke_v10_20261101.yaml"))
    authority = Path("output/SWE-RPG/threeway-readable-clean61-v1-20261026/gepa-source")
    for name in ("manifest.json", "instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (a.pce.dataset_snapshot / name).read_bytes() == (authority / name).read_bytes()
    assert not (new.dataset_snapshot / "learning_diagnostics").exists()
    supervisor = Path("configs/safe_pce_ace_rpg_codex_smoke_supervisor_v11_20261101.yaml")
    session, log, args, program = _load_launch_config(supervisor)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == new.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert launch.slice_time == "00:08:00"
    effective = materialize_pce_config(a.pce, json.loads(new.initial_rules_path.read_text()),
        tmp_path / "effective", run_dir=new.run_dir)
    assert effective.code_submission_protocol == "split_code_test_v1"
    for preparation, count in ((True, 3), (False, 1)):
        script = build_array_script(config=effective, batch_dir=tmp_path,
            indices=list(range(count)), attempt=1, preparation_only=preparation)
        assert "#SBATCH --array=" + ",".join(map(str, range(count))) + "\n" in script
        assert "#SBATCH --time=01:00:00" in script and "#SBATCH --mem=4G" in script
        if preparation:
            assert "ENV_FILE" not in script and "source " not in script
    raw = yaml.safe_load(path.read_text())["experiment_contract"]
    assert raw["launch_authorized"] and not raw["previous_output_import"]
    assert raw["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
