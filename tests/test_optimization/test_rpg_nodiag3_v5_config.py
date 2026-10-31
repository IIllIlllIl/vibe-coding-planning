"""Formal NoDiag preparation reuses the accepted smoke without a scheduler cap."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v5_20261027.yaml")
SOURCE = Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v5_20261027.yaml")


def test_formal_nodiag_preserves_accepted_agents_and_frozen_scientific_design(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    smoke = load_optimization_config("configs/gepa_safe_pce_rpg_codex_smoke_v3_20261027.yaml",
                                     require_api_keys=False)
    prior = load_optimization_config("configs/gepa_safe_pce_rpg_codex_nodiag3_v4_20261026.yaml",
                                     require_api_keys=False)
    verify_hybrid_snapshot(config)
    assert config.search == prior.search
    assert config.search == replace(smoke.search, max_iterations=3, min_proposals=3,
        max_metric_calls=156, projection_metric_calls=156, reflection_minibatch_size=16)
    assert config.hybrid == replace(smoke.hybrid, dataset_file_sha256=config.hybrid.dataset_file_sha256,
        prompt_bundle=config.hybrid.prompt_bundle, prompt_bundle_sha256=config.hybrid.prompt_bundle_sha256)
    new_prompts = yaml.safe_load(Path(config.hybrid.prompt_bundle).read_text())
    old_prompts = yaml.safe_load(Path(smoke.hybrid.prompt_bundle).read_text())
    assert new_prompts.keys() == old_prompts.keys()
    assert new_prompts['curator_task'] == old_prompts['curator_task']
    assert new_prompts['refiner_task'] == old_prompts['refiner_task']
    assert 'pce_evidence' in new_prompts['reflector_task']
    assert Path(config.hybrid.prompt_bundle) == Path('configs/prompts/safe_pce_hybrid_ace_v2.yaml').resolve()
    assert config.reflection == smoke.reflection
    assert config.initial_rules_path == smoke.initial_rules_path == prior.initial_rules_path
    backend = SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))
    train, validation = backend.load_snapshot()
    split = json.loads(Path("configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json").read_text())
    assert [case.instance_id for case in train] == split["train"]
    assert [case.instance_id for case in validation] == split["validation"]
    assert (len(train), len(validation)) == (46, 15)
    heldout = set(json.loads(Path("configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025/downstream-test-selection.json").read_text())["selected_instance_ids"])
    assert len(heldout) == 16
    assert not heldout & {case.instance_id for case in train + validation}
    assert "pylint-dev__pylint-9728" not in backend.pce.instance_ids
    assert backend.learning_diagnostics == {}
    assert not (config.dataset_snapshot / "learning_diagnostics").exists()
    previous = SafePCEBackend(replace(prior, execution=replace(prior.execution, backend="local")))
    accepted = SafePCEBackend(replace(smoke, execution=replace(smoke.execution, backend="local")))
    for field in ("plan", "code", "agent_backends", "plan_prompt", "code_prompt",
                  "plan_instance_template", "code_instance_template", "docker", "container",
                  "agent_network_disabled", "slurm_evaluator_timeout_outcome"):
        assert getattr(backend.pce, field) == getattr(accepted.pce, field) == getattr(previous.pce, field)
    for name in ("train.jsonl", "validation.jsonl"):
        assert (config.dataset_snapshot / name).read_bytes() == (prior.dataset_snapshot / name).read_bytes()
    for name in ("instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (backend.pce.dataset_snapshot / name).read_bytes() == (previous.pce.dataset_snapshot / name).read_bytes()
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(SOURCE)
    assert manifest["engineering_baseline_config_sha256"] == sha256(Path(
        "configs/gepa_safe_pce_rpg_codex_smoke_v3_20261027.yaml"))
    assert config.run_dir != prior.run_dir and config.run_dir != smoke.run_dir
    contract = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is False
    assert contract["previous_output_import"] is False
    assert contract["split_counts"] == {"train": 46, "validation": 15, "test": 16}


def test_formal_actual_worker_arrays_have_no_concurrency_suffix(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    backend = SafePCEBackend(config)
    seed = json.loads(config.initial_rules_path.read_text())
    effective = materialize_pce_config(backend.pce, seed, tmp_path / "effective",
                                        run_dir=config.run_dir)
    assert effective.hpc.max_running_array_tasks == config.hpc.max_running_array_tasks == 0
    assert (effective.hpc.cpus_per_task, effective.hpc.mem, effective.hpc.time) == (1, "4G", "01:00:00")
    assert (config.hpc.cpus_per_task, config.hpc.mem, config.hpc.time) == (1, "4G", "00:30:00")
    pce_indices = list(range(32))
    script = build_array_script(config=effective, batch_dir=tmp_path / "pce", indices=pce_indices, attempt=1)
    assert f"#SBATCH --array={','.join(map(str, pce_indices))}\n" in script
    ref_indices = list(range(16))
    script = HybridPhaseExecutor(config).script_for_wave(tmp_path / "ref", "reflector", ref_indices, 1)
    assert f"#SBATCH --array={','.join(map(str, ref_indices))}\n" in script
    launch = Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v5_20261027.yaml")
    session, _, args, program = _load_launch_config(launch)
    assert program == RESUME_SCRIPT and session == config.run_dir.name
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert args[args.index("--slice-time") + 1] == "00:08:00"
    assert args[args.index("--max-runs") + 1] == "60"
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
