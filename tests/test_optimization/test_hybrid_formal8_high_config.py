"""Freeze the high-effort extension's differences from the completed 3it run."""

import copy
import hashlib
from pathlib import Path

import yaml

from scripts.hpc_resume_loop import parse_args
from scripts.hpc_supervisor_service import _load_launch_config
from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.behavioral_hybrid_runtime import hybrid_limits, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config


BASELINE = Path("configs/gepa_swe_chat_hybrid_formal3_v9_20261015.yaml")
CONFIG = Path("configs/gepa_swe_chat_hybrid_formal8_high_v1_20261016.yaml")
LAUNCH = Path("configs/swe_chat_hybrid_formal8_high_supervisor_v1_20261016.yaml")


def test_only_effort_target_budget_and_run_identity_change():
    before = yaml.safe_load(BASELINE.read_text())
    after = yaml.safe_load(CONFIG.read_text())
    normalized = yaml.safe_load(
        CONFIG.read_text().replace("formal8-high-v1-20261016", "formal3-v9-20261015")
        .replace("formal8_high_v1_20261016", "formal3_v9_20261015")
    )
    expected = copy.deepcopy(before)
    expected["search"].update(max_iterations=8, min_proposals=8,
                              max_metric_calls=500, projection_metric_calls=463)
    for role in ("reflector_model", "curator_model", "refiner_model"):
        expected["hybrid"][role]["reasoning_effort"] = "high"
        assert after["hybrid"][role]["model"] == "gpt-6-sol"
    normalized.pop("experiment_contract")
    expected.pop("experiment_contract")
    assert normalized == expected
    contract = after["experiment_contract"]
    assert contract["engineering_baseline"] == before["experiment_contract"]["identity"]
    assert contract["launch_authorized"] is False
    assert contract["start_policy"] == "fresh_seed_no_import_from_prior_runs"
    assert contract["test_policy"] == "downstream_test_not_staged_or_used"
    assert contract["prompt_bundle_sha256"] == hashlib.sha256(
        Path(after["hybrid"]["prompt_bundle"]).read_bytes()).hexdigest()


def test_loaded_candidate_snapshot_and_budget():
    config = load_optimization_config(CONFIG, require_api_keys=False)
    verify_hybrid_snapshot(config)
    validate_candidate(config.initial_rules_path.read_text(), hybrid_limits(config))
    assert config.search.projection_metric_calls == 23 + 8 * (16 * 2 + 23)
    assert config.search.max_metric_calls >= config.search.projection_metric_calls
    assert config.search.reflection_minibatch_size == 16
    assert config.hybrid.checker_model == {}
    assert config.checker.model == "deepseek-v4-flash"
    assert config.hybrid.refiner_enabled
    assert config.run_dir.name == "hybrid-formal8-high-v1-20261016"


def test_existing_supervisor_parser_resolves_eight_iteration_target():
    session, log, arguments, _ = _load_launch_config(LAUNCH)
    parsed = parse_args(arguments)
    assert session == "swe-chat-hybrid-formal8-high-v1-20261016"
    assert session in log
    assert parsed.target_iterations == 8
    assert parsed.max_runs == 160
    assert parsed.require_clean_worktree
    assert parsed.offline_gepa
    assert "--reclaim-staging" in arguments
    assert "--reclaim-workspaces" in arguments
    assert "--remote-dir" not in arguments
    assert parsed.remote_run_snapshot.endswith("/output/SWE-chat/hybrid-formal8-high-v1-20261016")
    raw = yaml.safe_load(CONFIG.read_text())
    assert raw["hpc"]["cpus_per_task"] == 1
    assert raw["hpc"]["mem"] == "4G"
    assert arguments[arguments.index("--cpus") + 1] == "1"
    assert arguments[arguments.index("--mem") + 1] == "4G"
