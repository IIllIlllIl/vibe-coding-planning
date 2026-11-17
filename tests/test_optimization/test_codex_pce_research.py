"""Shared PCE contracts used by AR; the old script loop lives in Git history."""
import hashlib
import json
from pathlib import Path

import pytest

from src.optimization.codex_pce_research import ResearchConfig, load_config, prepare_research
from src.optimization.safe_pce_execution import load_pce_config, materialize_pce_config
from src.optimization.safe_pce_learning import SafePCEBackend, IncompletePCEEvaluation
from src.optimization.autoresearch_pce import PCEExperiments
from tests.test_optimization.test_safe_pce_learning import _prepared, _output


def _fixture(tmp_path):
    prepared = _prepared(tmp_path)
    manifest_path = prepared.dataset_snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for split in ("train", "validation"):
        path = prepared.dataset_snapshot / f"{split}.jsonl"
        row = json.loads(path.read_text().splitlines()[0])
        path.write_text(json.dumps(row) + "\n")
        manifest[f"{split}_instances"] = 1
        manifest[f"{split}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    seed = tmp_path / "seed.md"
    seed.write_bytes(b"# Seed\r\nInspect the task.\r\n")
    return ResearchConfig(prepared.dataset_snapshot, prepared.safe_pce_config, seed,
        tmp_path / "research", "Improve the planning guide using fixed Safe PCE evidence.", 3,
        {}, tmp_path / "unused-test.sif", "0" * 64,
        program=Path("configs/prompts/autoresearch_pce_program.md").resolve())


def _executor(monkeypatch, *, fail_once=False, unresolved=False):
    calls = []
    failed = False
    def execute(effective, mode, cases, *, backend):
        nonlocal failed
        assert backend == "local" and mode == "polybench_pce"
        calls.append((effective.run_dir, cases[0].instance_id, effective.planning_rules))
        validation = cases[0].instance_id == "Org__Repo-7"
        if fail_once and validation and "Explore" in effective.planning_rules and not failed:
            failed = True
            raise OSError("synthetic evaluator interruption")
        resolved = not unresolved and ("Explore" not in effective.planning_rules if validation else "Seed" not in effective.planning_rules)
        output = _output(cases[0].instance_id, resolved)
        output["plan"] = ("VALIDATION_PRIVATE_TOKEN" if validation else "TRAIN_ORIGINAL_TOKEN") + "\n" + "Long evidence\n" * 2000
        output["plan_sha256"] = hashlib.sha256(output["plan"].encode()).hexdigest()
        return [output]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", execute)
    return calls


def test_free_guide_only_changes_the_planner_artifact_binding(tmp_path):
    config = _fixture(tmp_path)
    _, base = load_pce_config(config.pce_config)
    guide = "\r\n自由文本 {{ missing }}\r\n{% irrelevant %}\n"
    effective = materialize_pce_config(base, guide, tmp_path / "effective")
    assert effective.planning_rules == guide
    assert "rule_number" not in effective.plan_instance_template
    assert "Use the supplied planning guide" in effective.plan_instance_template
    assert effective.plan_prompt == base.plan_prompt
    assert effective.code_prompt == base.code_prompt
    assert effective.code_instance_template == base.code_instance_template
    assert effective.plan == base.plan and effective.code == base.code
    assert effective.container == base.container
    assert effective.evaluator_timeout == base.evaluator_timeout
    assert effective.agent_network_disabled == base.agent_network_disabled
    assert effective.evaluator_network_disabled == base.evaluator_network_disabled


def test_changed_frozen_inputs_cannot_resume_another_experiment(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    _executor(monkeypatch)
    experiments = PCEExperiments(config, SafePCEBackend)
    experiments.evaluate("evaluate")
    config.seed.write_text("Changed seed")
    with pytest.raises(ValueError, match="inputs or configuration changed"):
        prepare_research(config, SafePCEBackend)


def test_operational_failure_never_becomes_an_unresolved_score(tmp_path, monkeypatch):
    config = _fixture(tmp_path)
    def broken(*args, **kwargs):
        return [{"status": "failed", "instance_id": "Org__Repo-1"}]
    monkeypatch.setattr("src.optimization.safe_pce_learning.execute_pce", broken)
    experiments = PCEExperiments(config, SafePCEBackend)
    with pytest.raises(IncompletePCEEvaluation):
        experiments.evaluate("evaluate")
    assert not (config.run_dir / "private/round-0000-train.json").exists()


def test_superseded_script_schema_is_not_a_maintained_execution_path():
    with pytest.raises(ValueError, match="prototype is archived"):
        load_config("configs/archive/codex_pce_research/codex_pce_research.example.yaml")
