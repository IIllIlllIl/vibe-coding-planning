"""Fresh private 3it inputs preserve science and version only Coder wording."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import _load_launch_config
from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_learning import SafePCEBackend
from src.optimization.safe_pce_execution import sha256


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v6_20261027.yaml")


def test_fresh_v6_preserves_design_and_bundles_only_coder_prompt_delta():
    config = load_optimization_config(CONFIG, require_api_keys=False)
    prior = load_optimization_config("configs/gepa_safe_pce_rpg_codex_nodiag3_v5_20261027.yaml", require_api_keys=False)
    verify_hybrid_snapshot(config)
    assert config.search == prior.search
    assert config.hybrid == replace(prior.hybrid, dataset_file_sha256=config.hybrid.dataset_file_sha256)
    assert config.reflection == prior.reflection
    assert config.initial_rules_path == prior.initial_rules_path
    assert config.run_dir != prior.run_dir
    assert (config.dataset_snapshot / PRIVATE_ARTIFACT_MARKER).is_file()
    assert not (config.dataset_snapshot / "learning_diagnostics").exists()
    backend, old_backend = SafePCEBackend(config), SafePCEBackend(prior)
    for field in ("plan", "code", "agent_backends", "plan_prompt", "code_prompt",
                  "plan_instance_template", "docker", "container", "agent_network_disabled",
                  "slurm_evaluator_timeout_outcome", "instance_ids"):
        assert getattr(backend.pce, field) == getattr(old_backend.pce, field)
    prompts = yaml.safe_load(Path("configs/prompts/safe_pce_native_codex_code_v3.yaml").read_text())["prompts"]
    assert backend.pce.code_instance_template == prompts["code_instance"]
    assert backend.pce.code_instance_template != old_backend.pce.code_instance_template
    for name in ("train.jsonl", "validation.jsonl"):
        assert (config.dataset_snapshot / name).read_bytes() == (prior.dataset_snapshot / name).read_bytes()
    for name in ("instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (backend.pce.dataset_snapshot / name).read_bytes() == (old_backend.pce.dataset_snapshot / name).read_bytes()
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v6_20261027.yaml"))
    assert manifest["engineering_baseline_config_sha256"] == sha256(Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v5_20261027.yaml"))
    contract = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is False and contract["previous_output_import"] is False
    assert contract["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    assert contract["learning_prompt_delta_from_v5"] == "none"
    assert "workspace_preparation_extraction_and_acceptance_before_formal_launch" in contract["prelaunch_requires"]


def test_v6_retains_resources_scheduler_control_and_clean_start():
    config = load_optimization_config(CONFIG, require_api_keys=False)
    backend = SafePCEBackend(config)
    assert config.hpc.max_running_array_tasks == backend.pce.hpc.max_running_array_tasks == 0
    assert (backend.pce.hpc.cpus_per_task, backend.pce.hpc.mem, backend.pce.hpc.time) == (1, "4G", "01:00:00")
    assert (config.hpc.cpus_per_task, config.hpc.mem, config.hpc.time) == (1, "4G", "00:30:00")
    session, _, args, _ = _load_launch_config(Path("configs/safe_pce_ace_rpg_codex_nodiag3_supervisor_v6_20261027.yaml"))
    assert session == config.run_dir.name
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
    assert "--recover-controller-error-type-once" not in args
    assert not config.run_dir.exists()  # Configuration preparation has not started a run.
