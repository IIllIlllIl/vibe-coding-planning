"""Formal RPG preparation: immutable split, unchanged execution, no launch."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from scripts.tools.freeze_swe_chat_behavioral_formal_split import _content_sha256
from src.optimization.config import load_optimization_config
from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.safe_pce_execution import load_pce_config, pce_cases
from src.swe_rpg_pce.config import load_swe_rpg_pce_config


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_formal3_v1_20261025.yaml")
PCE = Path("configs/safe_pce_ace_rpg_codex_formal3_source_v1_20261025.yaml")
FROZEN = Path("configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025")
SPLIT_SOURCE = Path("output/SWE-RPG/threeway-v1-20261025")


def test_formal3_changes_only_split_budget_and_identity_from_completed_rpg():
    old = load_optimization_config("configs/gepa_safe_pce_rpg_codex_smoke_v2_20261025.yaml", require_api_keys=False)
    new = load_optimization_config(CONFIG, require_api_keys=False)
    assert new.search == replace(old.search, max_iterations=3, min_proposals=3,
        max_metric_calls=156, projection_metric_calls=156, reflection_minibatch_size=16)
    assert new.hybrid == replace(old.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.reflection == old.reflection and new.task == old.task
    assert new.initial_rules_path == old.initial_rules_path
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts", "poll_interval_seconds"):
        assert getattr(new.hpc, field) == getattr(old.hpc, field)
    baseline = load_swe_rpg_pce_config("configs/safe_pce_ace_rpg_codex_smoke_source_v2_20261025.yaml", require_api_keys=False)
    current = load_swe_rpg_pce_config(PCE, require_api_keys=False)
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template", "code_prompt",
                  "code_instance_template", "plan_submission_protocol", "docker", "container",
                  "agent_network_disabled", "slurm_evaluator_timeout_outcome"):
        assert getattr(current, field) == getattr(baseline, field)
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts", "max_running_array_tasks"):
        assert getattr(current.hpc, field) == getattr(baseline.hpc, field)
    raw = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert raw["previous_output_import"] is raw["launch_authorized"] is False
    assert raw["budget"]["pce_evaluation_slots_without_cache_upper_bound"] == 15 + 3 * (32 + 15) == 156
    assert raw["split_counts"] == {"train": 47, "validation": 15, "test": 16}


def test_formal_snapshot_excludes_downstream_rows_images_and_reference_files():
    config = load_optimization_config(CONFIG, require_api_keys=False)
    verify_hybrid_snapshot(config)
    manifest = json.loads((FROZEN / "manifest.json").read_text())
    assert manifest["content_sha256"] == _content_sha256(manifest)
    for relative, digest in manifest["source_subset_file_sha256"].items():
        assert hashlib.sha256((SPLIT_SOURCE / relative).read_bytes()).hexdigest() == digest
    splits = json.loads((FROZEN / "split-selection.json").read_text())
    test_ids = set(json.loads((FROZEN / "downstream-test-selection.json").read_text())["selected_instance_ids"])
    training_ids, validation_ids = set(splits["train"]), set(splits["validation"])
    assert [len(training_ids), len(validation_ids), len(test_ids)] == [47, 15, 16]
    assert not (training_ids & validation_ids or (training_ids | validation_ids) & test_ids)
    mode, pce = load_pce_config(config.safe_pce_config)
    assert mode == "swe_rpg_pce"
    cases = pce_cases(mode, pce)
    assert {case.instance_id for case in cases} == training_ids | validation_ids
    assert set(pce.instance_ids) == training_ids | validation_ids
    images = json.loads(pce.image_manifest.read_text())["records"]
    assert {record["instance_id"] for record in images.values()} == training_ids | validation_ids
    for name in ("instances.jsonl", "images.json", "manifest.json", "evaluation/evaluate_docker.py"):
        assert (pce.dataset_snapshot / name).read_bytes() == (SPLIT_SOURCE / "gepa-source" / name).read_bytes()
    source_lines = {json.loads(line)["instance_id"]: line for line in
        Path("output/SWE-RPG/pce-inputs/python78-v1-20261024/instances.jsonl").read_bytes().splitlines(keepends=True)}
    for subset in (pce.dataset_snapshot, SPLIT_SOURCE / "downstream-test-source"):
        for line in (subset / "instances.jsonl").read_bytes().splitlines(keepends=True):
            assert line == source_lines[json.loads(line)["instance_id"]]
        assert not list(subset.rglob("*mid-groundtruth*"))
    for case in cases:
        visible = case.agent_projection()
        assert not {"gold_patch", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "PR_Body"} & visible.keys()
    assert pce.run_dir == config.run_dir / "pce_evaluations"
    frozen = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert frozen["source_pce_config_sha256"] == hashlib.sha256(PCE.read_bytes()).hexdigest()


def test_formal_preserves_shared_wave_supervisor_and_preheat_modules(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    script = HybridPhaseExecutor(config).script_for_wave(tmp_path / "wave", "reflector", list(range(16)), 1)
    expected = "#SBATCH --array=" + ",".join(map(str, range(16)))
    if config.hpc.max_running_array_tasks:
        expected += "%" + str(config.hpc.max_running_array_tasks)
    assert expected + "\n" in script
    assert "src.optimization.behavioral_hybrid_worker" in script
    session, log, args, program = _load_launch_config(Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_formal3_supervisor_v1_20261025.yaml"))
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert args[args.index("--max-runs") + 1] == "60"
    assert not {"--remote-dir", "--remote-dataset-dir", "--remote-run-dir"} & set(args)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces", "--submit"} <= set(args)
    history = load_swe_rpg_pce_config("configs/swe_rpg_history_gepa62_v1_20261025.yaml", require_api_keys=False)
    assert not history.hpc.submit
    assert set(history.instance_ids) == set(load_swe_rpg_pce_config(PCE, require_api_keys=False).instance_ids)
    assert "operations/swe-rpg-gepa62-history" in str(history.run_dir)
    standard = load_swe_rpg_pce_config("configs/swe_rpg_safe_pce.example.yaml", require_api_keys=False)
    assert standard.agent_backends == {} and standard.plan.temperature == 1
    assert standard.plan.thinking == "disabled"
