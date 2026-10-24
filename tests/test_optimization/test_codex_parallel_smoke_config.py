"""Frozen v5 configuration comparison; no models, containers or scheduler."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import load_pce_config, pce_cases, validate_learning_source
from src.swe_verified_pce.config import load_swe_verified_pce_config


OLD = Path("configs/gepa_safe_pce_verified_codex_smoke_v4_20261023.yaml")
NEW = Path("configs/gepa_safe_pce_verified_codex_smoke_v5_20261024.yaml")
OLD_PCE = Path("configs/safe_pce_ace_verified_codex_smoke_source_v4_20261023.yaml")
NEW_PCE = Path("configs/safe_pce_ace_verified_codex_smoke_source_v5_20261024.yaml")
SUPERVISOR = Path("configs/safe_pce_ace_verified_codex_smoke_supervisor_v5_20261024.yaml")


def configurations():
    return (load_optimization_config(path, require_api_keys=False) for path in (OLD, NEW))


def test_parallel_smoke_preserves_search_prompts_models_and_resources():
    old, new = configurations()
    assert yaml.safe_load(NEW.read_text())["mode"] == yaml.safe_load(OLD.read_text())["mode"] == "safe_pce_hybrid_ace"
    assert new.search == old.search
    assert new.reflection == old.reflection
    assert new.initial_rules_path == old.initial_rules_path
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.dataset_snapshot != old.dataset_snapshot and new.run_dir != old.run_dir
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts",
                  "poll_interval_seconds", "max_running_array_tasks"):
        assert getattr(new.hpc, field) == getattr(old.hpc, field)
    raw = yaml.safe_load(NEW.read_text())
    contract = raw["experiment_contract"]
    assert contract["launch_authorized"] is contract["previous_output_import"] is False
    assert contract["authentication_restore_commit"] == "8351de4"
    assert contract["completed_execution_baseline_config"] == str(OLD)
    assert contract["changed_axes"] == ["restored_parallel_per_call_native_auth_without_whole_call_lease"]
    assert str(new.hpc.worker_config_path).endswith(str(NEW))
    assert NEW.stem in raw["hpc"]["remote_project_dir"]


def test_pce_source_has_no_unintended_experimental_changes():
    previous = yaml.safe_load(OLD_PCE.read_text())
    current = yaml.safe_load(NEW_PCE.read_text())
    for value in (previous, current):
        value.pop("experiment_contract")
        value.pop("purpose")
        value["paths"].pop("run_dir")
        for field in ("job_name_prefix", "worker_config_path"):
            value["hpc"].pop(field)
    assert previous == current
    old = load_swe_verified_pce_config(OLD_PCE, require_api_keys=False)
    new = load_swe_verified_pce_config(NEW_PCE, require_api_keys=False)
    for field in ("plan", "code", "agent_backends", "docker", "container", "instance_ids",
                  "plan_prompt", "plan_instance_template", "code_prompt", "code_instance_template",
                  "image_manifest", "plan_submission_protocol", "slurm_evaluator_timeout_outcome"):
        assert getattr(old, field) == getattr(new, field)
    assert new.agent_network_disabled is True
    assert new.hpc.max_running_array_tasks == 3
    assert new.hpc.cpus_per_task == 1 and new.hpc.mem == "4G"


def test_prepared_snapshot_preserves_exact_cases_rows_images_and_resolved_prompts():
    old, new = configurations()
    verify_hybrid_snapshot(new)
    for name in ("train.jsonl", "validation.jsonl", "pce_source/manifest.json",
                 "pce_source/instances.jsonl", "pce_source/images.json", "pce_source/selection_manifest.json"):
        assert (new.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    old_mode, old_pce = load_pce_config(old.safe_pce_config)
    mode, pce = load_pce_config(new.safe_pce_config)
    validate_learning_source(pce)
    assert mode == old_mode == "swe_verified_pce"
    for field in ("plan_prompt", "plan_instance_template", "code_prompt", "code_instance_template",
                  "agent_backends", "agent_network_disabled"):
        assert getattr(pce, field) == getattr(old_pce, field)
    assert {case.instance_id for case in pce_cases(mode, pce)} == {
        "astropy__astropy-13033", "pallets__flask-5014", "pytest-dev__pytest-10051"}
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == hashlib.sha256(NEW_PCE.read_bytes()).hexdigest()
    assert manifest["engineering_baseline_config_sha256"] == hashlib.sha256(OLD.read_bytes()).hexdigest()
    assert (manifest["train_instances"], manifest["validation_instances"]) == (2, 1)
    assert pce.run_dir == new.run_dir / "pce_evaluations"


def test_reflector_wave_remains_a_two_element_array_not_a_serial_loop(tmp_path):
    _, new = configurations()
    script = HybridPhaseExecutor(new).script_for_wave(tmp_path / "batch", "reflector", [0, 1], 1)
    assert "#SBATCH --array=0,1\n" in script
    assert "#SBATCH --cpus-per-task=1" in script and "#SBATCH --mem=4G" in script
    assert "src.optimization.behavioral_hybrid_worker" in script
    assert str(NEW) in script


def test_supervisor_preserves_cleanup_slices_resources_and_fresh_identity():
    from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
    old = yaml.safe_load(Path("configs/archive/supervisor_launches/safe_pce_ace_verified_codex_smoke_supervisor_v4_20261023.yaml").read_text())
    new = yaml.safe_load(SUPERVISOR.read_text())
    previous, current = old["arguments"], new["arguments"]
    for option in ("--poll-interval", "--slice-time", "--max-runs", "--cpus", "--mem", "--ulhpc-config"):
        assert previous[previous.index(option) + 1] == current[current.index(option) + 1]
    assert current[current.index("--gepa-config") + 1] == str(NEW)
    assert old["session"] != new["session"] and old["log"] != new["log"]
    assert new["session"] in current[current.index("--state-file") + 1]
    for flag in ("--require-clean-worktree", "--gepa-rules", "--reclaim-staging", "--reclaim-workspaces"):
        assert flag in current
    assert not any(flag in current for flag in ("--remote-dir", "--remote-run-dir", "--remote-dataset-dir"))
    session, log, arguments, program = _load_launch_config(SUPERVISOR)
    assert (session, log, arguments, program) == (new["session"], new["log"], current, RESUME_SCRIPT)
