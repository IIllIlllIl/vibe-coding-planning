"""Minimal accepted-case smoke exercises the reviewed production contracts."""
from dataclasses import replace
import json
from pathlib import Path

import yaml
import pytest

from scripts.hpc_supervisor_service import _load_launch_config
from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


@pytest.fixture(params=[("v4_20261027", True), ("v5_20261028", False)])
def smoke_binding(request):
    suffix, launch_authorized = request.param
    return (Path(f"configs/gepa_safe_pce_rpg_codex_smoke_{suffix}.yaml"),
            Path(f"configs/safe_pce_ace_rpg_codex_smoke_source_{suffix}.yaml"),
            Path(f"configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_{suffix}.yaml"),
            launch_authorized)


def test_smoke_uses_only_completed_development_cases_with_reviewed_inputs(smoke_binding):
    config_path, source_path, _, launch_authorized = smoke_binding
    config = load_optimization_config(config_path, require_api_keys=False)
    prior = load_optimization_config(
        "configs/gepa_safe_pce_rpg_codex_smoke_v3_20261027.yaml", require_api_keys=False)
    formal = load_optimization_config(
        "configs/gepa_safe_pce_rpg_codex_nodiag3_v7_20261027.yaml", require_api_keys=False)
    verify_hybrid_snapshot(config)
    backend, old, production = SafePCEBackend(config), SafePCEBackend(prior), SafePCEBackend(formal)
    local = SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))
    train, validation = local.load_snapshot()
    assert [case.instance_id for case in train] == [
        "falconry__falcon-2540", "openai__openai-agents-python-454"]
    assert [case.instance_id for case in validation] == ["pylint-dev__pylint-10797"]
    assert {case.source.working_directory for case in train + validation} == {"/testbed", "/testbed2"}
    assert config.search == prior.search
    assert config.initial_rules_path == prior.initial_rules_path == formal.initial_rules_path
    assert config.hybrid == replace(formal.hybrid, dataset_file_sha256=config.hybrid.dataset_file_sha256)
    assert backend.pce.workspace_preparation == "separate_slurm_v1"
    for name in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                 "code_prompt", "code_instance_template", "container", "docker",
                 "agent_network_disabled", "slurm_evaluator_timeout_outcome"):
        assert getattr(backend.pce, name) == getattr(production.pce, name)
    assert backend.pce.plan == old.pce.plan and backend.pce.code == old.pce.code
    for name in ("train.jsonl", "validation.jsonl"):
        assert (config.dataset_snapshot / name).read_bytes() == (prior.dataset_snapshot / name).read_bytes()
    for name in ("instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (backend.pce.dataset_snapshot / name).read_bytes() == (old.pce.dataset_snapshot / name).read_bytes()
    assert (config.dataset_snapshot / PRIVATE_ARTIFACT_MARKER).exists()
    assert local.learning_diagnostics == {}
    assert not (config.dataset_snapshot / "learning_diagnostics").exists()
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(source_path)
    assert yaml.safe_load(source_path.read_text())["hpc"]["worker_config_path"] == str(source_path)
    contract = yaml.safe_load(config_path.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is launch_authorized and contract["development_only"]
    assert contract["previous_output_import"] is False
    assert contract["budget"]["pce_evaluation_slots_without_cache"] == {"minimum": 5, "maximum": 6}
    assert contract["budget"]["preparation_elements_before_retries"] == {"minimum": 15, "maximum": 18}


def test_smoke_uses_existing_supervisor_and_unthrottled_no_model_preparation(tmp_path, smoke_binding):
    config_path, _, supervisor_path, _ = smoke_binding
    config = load_optimization_config(config_path, require_api_keys=False)
    backend = SafePCEBackend(config)
    effective = materialize_pce_config(backend.pce, json.loads(config.initial_rules_path.read_text()),
                                       tmp_path / "effective", run_dir=config.run_dir)
    assert effective.workspace_preparation == "separate_slurm_v1"
    assert effective.hpc.max_running_array_tasks == config.hpc.max_running_array_tasks == 0
    assert (effective.hpc.cpus_per_task, effective.hpc.mem, effective.hpc.time) == (1, "4G", "01:00:00")
    assert (config.hpc.cpus_per_task, config.hpc.mem, config.hpc.time) == (1, "4G", "00:30:00")
    script = build_array_script(config=effective, batch_dir=tmp_path / "prep",
                                indices=list(range(6)), attempt=1, preparation_only=True)
    array_line = next(line for line in script.splitlines() if line.startswith("#SBATCH --array="))
    assert "%" not in array_line and len(array_line.split("=")[1].split(",")) == 6
    assert "source " not in script and "ENV_FILE" not in script
    assert " -m src.swe_verified_pce.worker " in script
    session, _, args, _ = _load_launch_config(supervisor_path)
    assert session == config.run_dir.name
    assert args[args.index("--gepa-config") + 1] == str(config_path)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
    assert "--recover-controller-error-type-once" not in args


def test_v5_retains_v4_science_and_only_adds_publication_regression_acceptance():
    current = load_optimization_config(
        "configs/gepa_safe_pce_rpg_codex_smoke_v5_20261028.yaml", require_api_keys=False)
    old = load_optimization_config(
        "configs/gepa_safe_pce_rpg_codex_smoke_v4_20261027.yaml", require_api_keys=False)
    assert current.search == old.search and current.reflection == old.reflection
    assert current.hybrid == replace(old.hybrid, dataset_file_sha256=current.hybrid.dataset_file_sha256)
    assert current.initial_rules_path == old.initial_rules_path
    assert current.run_dir != old.run_dir
    source, prior = SafePCEBackend(current).pce, SafePCEBackend(old).pce
    for name in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                 "code_prompt", "code_instance_template", "container", "docker",
                 "agent_network_disabled", "slurm_evaluator_timeout_outcome", "workspace_preparation"):
        assert getattr(source, name) == getattr(prior, name)
    contract = yaml.safe_load(Path(
        "configs/gepa_safe_pce_rpg_codex_smoke_v5_20261028.yaml").read_text())["experiment_contract"]
    assert "metadata_failure_never_changes_scores_retries_checkpoints_or_learning" in contract["acceptance"]
