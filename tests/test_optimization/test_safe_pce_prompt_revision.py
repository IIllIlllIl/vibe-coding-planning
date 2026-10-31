"""Versioned prompt revisions keep frozen inputs and workflow control intact."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from src.optimization.behavioral_hybrid import HybridLimits, checker_projection, validate_candidate
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_learning import SafePCEBackend
from src.optimization.safe_pce_preparation import prepare
from src.optimization.safe_pce_execution import sha256
from tests.test_optimization.test_safe_pce_learning import _prepared


PROMPTS = Path("configs/prompts")
PLANNER = PROMPTS / "safe_pce_rules_planner_v2.yaml"
CODER = PROMPTS / "safe_pce_native_codex_code_v2.yaml"
SEED = PROMPTS / "safe_pce_hybrid_seed_v2.json"


def test_v2_changes_only_environment_context_and_supplied_plan_label():
    old = yaml.safe_load((PROMPTS / "safe_pce_rules_planner_v1.yaml").read_text())["prompts"]["plan_system"]
    new = yaml.safe_load(PLANNER.read_text())["prompts"]["plan_system"]
    before, tail = old.split("Use the project environment", 1)
    old_environment, after = tail.split("Use available tools", 1)
    assert new.startswith(before)
    assert new.split("Use available tools", 1)[1] == after
    assert "/opt/miniconda3/envs" in old_environment
    assert "/opt/miniconda3/envs" not in new
    assert "repository documentation and installed tools" in new
    assert "N/R/P/V" not in new and "NRPV" not in new

    old = yaml.safe_load((PROMPTS / "safe_pce_native_codex_code_v1.yaml").read_text())["prompts"]
    new = yaml.safe_load(CODER.read_text())["prompts"]
    assert old["code_instance"] == new["code_instance"]
    assert old["code_system"].split("Approved Plan:", 1)[1] == new["code_system"].split("Supplied Plan:", 1)[1]
    assert "approved" not in new["code_system"].lower()
    assert "/opt/miniconda3/envs" not in new["code_system"]
    assert "{{plan}}" in new["code_system"]


def test_seed_is_a_placeholder_with_the_historical_guide():
    old = json.loads((PROMPTS / "swe_chat_hybrid_seed_v1.json").read_text())
    new = json.loads(SEED.read_text())
    limits = HybridLimits(10, 200, 40, 80, condition_tokens=48, item_guide_tokens=96,
                         shared_guide_enabled=False)
    assert validate_candidate(SEED.read_text(), limits) == new
    assert len(new["checklist"]) == 1 and "shared_guide" not in new
    assert new["checklist"][0]["condition"] == "Is the Plan a placeholder?"
    assert new["checklist"][0]["guide"] == old["checklist"][0]["guide"]
    projection = checker_projection(new)
    assert projection["checklist"][0]["rule_number"] == 1
    assert "id" not in projection["checklist"][0]


def test_prepare_explicit_versions_without_overwriting_old_snapshot(tmp_path):
    old_config = _prepared(tmp_path)
    old = SafePCEBackend(old_config)
    train, validation = old.load_snapshot()
    old_prompt = old.pce.plan_prompt
    old_manifest = (old_config.dataset_snapshot / "manifest.json").read_bytes()
    source = yaml.safe_load(old_config.safe_pce_config.read_text())
    source["prompts"].update(yaml.safe_load(CODER.read_text())["prompts"])
    source_path = tmp_path / "revised-pce.yaml"
    source_path.write_text(yaml.safe_dump(source))
    target = tmp_path / "revised-learning.yaml"
    prepare(learning_config=tmp_path / "learning.yaml", pce_config=source_path,
            selection={"train": [c.instance_id for c in train],
                       "validation": [c.instance_id for c in validation]},
            snapshot=tmp_path / "revised-inputs", run_dir=tmp_path / "revised-run",
            output_config=target, planner_prompt=PLANNER, initial_guideline=SEED)
    current = load_optimization_config(target, require_api_keys=False)
    backend = SafePCEBackend(current)
    other_train, other_validation = backend.load_snapshot()
    assert [c.instance_id for c in train + validation] == [
        c.instance_id for c in other_train + other_validation]
    assert backend.pce.plan_prompt == yaml.safe_load(PLANNER.read_text())["prompts"]["plan_system"]
    assert backend.pce.code_prompt == source["prompts"]["code_system"]
    assert current.initial_rules_path == SEED.resolve()
    assert current.search == old_config.search
    for field in ("plan", "code", "hpc", "container"):
        left, right = getattr(old.pce, field), getattr(backend.pce, field)
        if field == "hpc":
            left, right = replace(left, worker_config_path=""), replace(right, worker_config_path="")
        assert left == right
    manifest = json.loads((current.dataset_snapshot / "manifest.json").read_text())
    assert manifest["planner_prompt_source"]["sha256"] == sha256(PLANNER)
    assert manifest["initial_guideline_source"]["sha256"] == sha256(SEED)
    assert (old_config.dataset_snapshot / "manifest.json").read_bytes() == old_manifest
    assert old.pce.plan_prompt == old_prompt
    with pytest.raises(ValueError, match="prepared input already exists with different bytes"):
        prepare(learning_config=tmp_path / "learning.yaml", pce_config=source_path,
                selection={"train": [c.instance_id for c in train],
                           "validation": [c.instance_id for c in validation]},
                snapshot=tmp_path / "revised-inputs", run_dir=tmp_path / "revised-run",
                output_config=target, initial_guideline=SEED)


def test_omitted_options_preserve_historical_prompt_and_seed_defaults(tmp_path):
    config = _prepared(tmp_path)
    backend = SafePCEBackend(config)
    assert backend.pce.plan_prompt == yaml.safe_load(
        (PROMPTS / "safe_pce_rules_planner_v1.yaml").read_text())["prompts"]["plan_system"]
    assert config.initial_rules_path == (PROMPTS / "safe_pce_hybrid_seed_v1.json").resolve()
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    assert "planner_prompt_source" not in manifest and "initial_guideline_source" not in manifest


def test_existing_cli_forwards_version_selection_without_another_preparer(tmp_path, monkeypatch):
    from scripts.tools import prepare_safe_pce_ace_gepa as cli
    captured = {}

    def prepared(**kwargs):
        captured.update(kwargs)
        return kwargs["output_config"]

    monkeypatch.setattr(cli, "prepare", prepared)
    monkeypatch.setattr("sys.argv", [
        "prepare_safe_pce_ace_gepa",
        "--learning-config", "baseline.yaml", "--pce-config", "pce.yaml",
        "--split-selection", "configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json",
        "--snapshot", str(tmp_path / "inputs"), "--run-dir", str(tmp_path / "run"),
        "--output-config", str(tmp_path / "config.yaml"),
        "--planner-prompt", str(PLANNER), "--initial-guideline", str(SEED),
    ])
    cli.main()
    assert captured["planner_prompt"] == PLANNER
    assert captured["initial_guideline"] == SEED
    assert len(captured["selection"]["train"]) == 46


def test_v4_formal_arms_freeze_reviewed_prompts_seed_and_reference_boundary():
    from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT

    results = []
    selection = json.loads(Path(
        "configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json"
    ).read_text())
    for arm in ("nodiag", "diag"):
        path = Path(f"configs/gepa_safe_pce_rpg_codex_{arm}3_v4_20261026.yaml")
        config = load_optimization_config(path, require_api_keys=False)
        backend = SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))
        train, validation = backend.load_snapshot()
        assert [case.instance_id for case in train] == selection["train"]
        assert [case.instance_id for case in validation] == selection["validation"]
        assert config.initial_rules_path == SEED.resolve()
        assert backend.pce.plan_prompt == yaml.safe_load(PLANNER.read_text())["prompts"]["plan_system"]
        coder = yaml.safe_load(CODER.read_text())["prompts"]
        assert backend.pce.code_prompt == coder["code_system"]
        assert backend.pce.code_instance_template == coder["code_instance"]
        assert config.search.max_iterations == 3
        assert config.search.reflection_minibatch_size == 16
        assert config.search.max_metric_calls == 156
        assert config.hybrid.max_items == 10
        assert config.hybrid.condition_tokens == 48
        assert config.hybrid.item_guide_tokens == 96
        assert config.hybrid.shared_guide_enabled is False
        manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
        assert manifest["planner_prompt_source"]["sha256"] == sha256(PLANNER)
        assert manifest["initial_guideline_source"]["sha256"] == sha256(SEED)
        if arm == "nodiag":
            assert backend.learning_diagnostics == {}
            assert "learning_diagnostics" not in manifest
            assert not (config.dataset_snapshot / "learning_diagnostics").exists()
        else:
            assert set(backend.learning_diagnostics) == set(selection["train"])
            assert sum(len(item["references"]) for item in backend.learning_diagnostics.values()) == 92
        contract = yaml.safe_load(path.read_text())["experiment_contract"]
        assert contract["launch_authorized"] == (arm == "nodiag")
        launch = Path(f"configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_{arm}3_supervisor_v4_20261026.yaml")
        session, log, args, program = _load_launch_config(launch)
        assert program == RESUME_SCRIPT
        assert session == config.run_dir.name and session in log
        assert args[args.index("--gepa-config") + 1] == str(path)
        assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
        results.append(backend)
    no, yes = results
    assert no.config.search == yes.config.search
    for field in ("plan", "code", "plan_prompt", "code_prompt", "plan_instance_template",
                  "code_instance_template", "container", "docker"):
        assert getattr(no.pce, field) == getattr(yes.pce, field)
