"""Declared eligibility exclusion; no outcome rewriting or workflow changes."""
from dataclasses import replace
from functools import lru_cache
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_rpg_pce.dataset import issue_text


EXCLUDED = "pylint-dev__pylint-9728"
FROZEN = Path("configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026")
PARENT = Path("configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025")
SOURCE = Path("output/SWE-RPG/pce-inputs/python78-v1-20261024")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache
def backend(arm):
    config = load_optimization_config(
        f"configs/gepa_safe_pce_rpg_codex_{arm}3_v3_20261026.yaml",
        require_api_keys=False,
    )
    config = replace(config, execution=replace(config.execution, backend="local"))
    result = SafePCEBackend(config)
    train, validation = result.load_snapshot()
    return result, train, validation


def test_overlay_removes_only_one_training_task_without_repartitioning():
    original = json.loads((PARENT / "split-selection.json").read_text())
    cleaned = json.loads((FROZEN / "split-selection.json").read_text())
    ledger = json.loads((FROZEN / "manifest.json").read_text())
    assert EXCLUDED in original["train"]
    assert cleaned["train"] == [item for item in original["train"] if item != EXCLUDED]
    assert cleaned["validation"] == original["validation"]
    assert ledger["counts"] == {
        "train": 46, "validation": 15, "downstream_test": 16, "excluded_train": 1,
    }
    assert ledger["split_selection_sha256"] == sha(FROZEN / "split-selection.json")
    assert ledger["parent_split_content_sha256"] == json.loads(
        (PARENT / "manifest.json").read_text()
    )["content_sha256"]
    assert ledger["official_scoring_unchanged"] is True
    assert ledger["host_incomplete_guard_unchanged"] is True
    assert ledger["launch_authorized"] is False
    heldout = json.loads(Path(ledger["downstream_test_selection"]).read_text())
    assert len(heldout["selected_instance_ids"]) == 16
    assert not set(heldout["selected_instance_ids"]) & set(
        cleaned["train"] + cleaned["validation"]
    )


def test_exclusion_evidence_matches_original_issue_and_test_patch():
    raw = next(
        line for line in (SOURCE / "instances.jsonl").read_bytes().splitlines(keepends=True)
        if json.loads(line)["instance_id"] == EXCLUDED
    )
    row = json.loads(raw)
    issue = issue_text(row)
    evidence = json.loads((FROZEN / "manifest.json").read_text())["excluded"][0]
    assert "tox.ini" in issue
    assert evidence["old_helper"] not in issue
    assert evidence["test_required_helper"] not in issue
    assert "-def _cfg_has_config(" in row["patch"]
    assert "+def _cfg_or_ini_has_config(" in row["patch"]
    assert "+    _cfg_or_ini_has_config," in row["test_patch"]
    assert hashlib.sha256(raw).hexdigest() == evidence["source_row_with_line_ending_sha256"]
    for field, content in (
        ("decision_time_issue_sha256", issue),
        ("gold_patch_sha256", row["patch"]),
        ("test_patch_sha256", row["test_patch"]),
    ):
        assert hashlib.sha256(content.encode()).hexdigest() == evidence[field]


@pytest.mark.parametrize("arm", ["nodiag", "diag"])
def test_effective_inputs_exclude_task_rows_images_and_reference_files(arm):
    result, train, validation = backend(arm)
    cleaned = json.loads((FROZEN / "split-selection.json").read_text())
    assert [item.instance_id for item in train] == cleaned["train"]
    assert [item.instance_id for item in validation] == cleaned["validation"]
    expected = set(cleaned["train"] + cleaned["validation"])
    assert EXCLUDED not in expected
    assert set(result.pce.instance_ids) == expected
    images = json.loads(result.pce.image_manifest.read_text())["records"]
    assert {record["instance_id"] for record in images.values()} == expected
    assert not list(result.config.dataset_snapshot.rglob("*9728*"))
    assert EXCLUDED not in result.learning_diagnostics
    if arm == "diag":
        assert set(result.learning_diagnostics) == set(cleaned["train"])
        assert sum(len(record["references"]) for record in result.learning_diagnostics.values()) == 92
    else:
        assert result.learning_diagnostics == {}


@pytest.mark.parametrize("arm", ["nodiag", "diag"])
def test_selected_raw_bytes_and_official_evaluator_are_unchanged(arm):
    result, _, _ = backend(arm)
    source_lines = {
        json.loads(line)["instance_id"]: line
        for line in (SOURCE / "instances.jsonl").read_bytes().splitlines(keepends=True)
    }
    selected = (result.pce.dataset_snapshot / "instances.jsonl").read_bytes().splitlines(keepends=True)
    assert len(selected) == 61
    for line in selected:
        assert line == source_lines[json.loads(line)["instance_id"]]
    evaluator = "evaluation/evaluate_docker.py"
    assert (result.pce.dataset_snapshot / evaluator).read_bytes() == (SOURCE / evaluator).read_bytes()
    ledger = json.loads((FROZEN / "manifest.json").read_text())
    assert sha(Path(ledger["runtime_source_snapshot"]) / "manifest.json") == ledger["runtime_source_manifest_sha256"]


def test_arms_preserve_identical_models_prompts_budget_and_execution():
    no, train, validation = backend("nodiag")
    yes, other_train, other_validation = backend("diag")
    assert no.config.search == yes.config.search
    assert no.config.search.reflection_minibatch_size == 16
    assert no.config.search.max_iterations == 3
    assert no.config.search.max_metric_calls == 15 + 3 * (2 * 16 + 15) == 156
    assert no.config.initial_rules_path == yes.config.initial_rules_path
    assert no.config.reflection == yes.config.reflection
    assert replace(no.config.hybrid, dataset_file_sha256={}) == replace(
        yes.config.hybrid, dataset_file_sha256={}
    )
    assert all(
        left.planner_input() == right.planner_input()
        for left, right in zip(train + validation, other_train + other_validation)
    )
    for field in (
        "plan", "code", "agent_backends", "plan_prompt", "code_prompt",
        "plan_instance_template", "code_instance_template", "docker", "container",
    ):
        assert getattr(no.pce, field) == getattr(yes.pce, field)
    for name in ("train.jsonl", "validation.jsonl"):
        assert (no.config.dataset_snapshot / name).read_bytes() == (
            yes.config.dataset_snapshot / name
        ).read_bytes()


@pytest.mark.parametrize("arm", ["nodiag", "diag"])
def test_supervisors_point_to_new_clean_inputs_without_importing_old_runs(arm):
    result, _, _ = backend(arm)
    config_path = Path(f"configs/gepa_safe_pce_rpg_codex_{arm}3_v3_20261026.yaml")
    contract = yaml.safe_load(config_path.read_text())["experiment_contract"]
    assert contract["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    assert contract["excluded_from_both_arms"] == [EXCLUDED]
    assert contract["launch_authorized"] is False
    assert contract["start_policy"] == "fresh_seed_no_output_checkpoint_candidate_or_counter_import"
    path = Path(f"configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_{arm}3_supervisor_v3_20261026.yaml")
    session, log, args, program = _load_launch_config(path)
    assert program == RESUME_SCRIPT
    assert session == result.config.run_dir.name and session in log
    assert args[args.index("--gepa-config") + 1] == str(config_path)
    assert {"--require-clean-worktree", "--reclaim-staging", "--reclaim-workspaces"} <= set(args)
