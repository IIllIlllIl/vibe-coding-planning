"""Separate workspace preparation changes transport, not the frozen experiment."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import _load_launch_config
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v7_20261027.yaml")


def test_prepared_formal_run_retains_learning_and_agent_inputs():
    config = load_optimization_config(CONFIG, require_api_keys=False)
    prior = load_optimization_config("configs/gepa_safe_pce_rpg_codex_nodiag3_v6_20261027.yaml", require_api_keys=False)
    verify_hybrid_snapshot(config)
    assert config.search == prior.search
    assert config.hybrid == replace(prior.hybrid, dataset_file_sha256=config.hybrid.dataset_file_sha256)
    assert config.initial_rules_path == prior.initial_rules_path
    backend, old = SafePCEBackend(config), SafePCEBackend(prior)
    assert backend.pce.workspace_preparation == "separate_slurm_v1"
    assert old.pce.workspace_preparation == "inline"
    for name in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                 "code_prompt", "code_instance_template", "container", "docker", "agent_network_disabled",
                 "slurm_evaluator_timeout_outcome", "instance_ids"):
        assert getattr(backend.pce, name) == getattr(old.pce, name)
    for name in ("train.jsonl", "validation.jsonl"):
        assert (config.dataset_snapshot / name).read_bytes() == (prior.dataset_snapshot / name).read_bytes()
    for name in ("instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (backend.pce.dataset_snapshot / name).read_bytes() == (old.pce.dataset_snapshot / name).read_bytes()
    assert not (config.dataset_snapshot / "learning_diagnostics").exists()
    contract = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is False and contract["previous_output_import"] is False
    assert contract["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v7_20261027.yaml"))


def test_materialized_config_and_preparation_script_keep_resources_and_no_model_credentials(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    backend = SafePCEBackend(config)
    effective = materialize_pce_config(backend.pce, json.loads(config.initial_rules_path.read_text()),
                                        tmp_path / "effective", run_dir=config.run_dir)
    assert effective.workspace_preparation == "separate_slurm_v1"
    assert effective.hpc.max_running_array_tasks == config.hpc.max_running_array_tasks == 0
    assert (effective.hpc.cpus_per_task, effective.hpc.mem, effective.hpc.time) == (1, "4G", "01:00:00")
    script = build_array_script(config=effective, batch_dir=tmp_path / "prep", indices=list(range(48)),
                                attempt=1, preparation_only=True)
    spec = next(line for line in script.splitlines() if line.startswith("#SBATCH --array="))
    assert "%" not in spec and len(spec.split("=")[1].split(",")) == 48
    assert "source " not in script and "ENV_FILE" not in script
    assert " -m src.swe_verified_pce.worker " in script
    session, _, args, _ = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v7_20261027.yaml"))
    assert session == config.run_dir.name
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
