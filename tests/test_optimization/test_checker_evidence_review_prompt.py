from pathlib import Path

import yaml

from src.optimization.playbook_runtime import _render


PROMPTS = Path("configs/prompts/offline_gepa_paired_binary_ace_codex_v8_evidence_review_20260927.yaml")
FROZEN = Path("configs/prompts/offline_gepa_paired_binary_ace_codex_v7_consolidated_goal_20260927.yaml")


def test_evidence_review_only_changes_checker_system():
    current = yaml.safe_load(PROMPTS.read_text())
    previous = yaml.safe_load(FROZEN.read_text())
    assert current.keys() == previous.keys()
    for key in previous:
        if key not in {"prompt_id", "checker_system"}:
            assert current[key] == previous[key]
    checker = " ".join(current["checker_system"].split())
    assert "behavior requested by the task" in checker
    assert "Search matches locate relevant material" in checker
    assert "supporting and ruling out" in checker
    assert "including after another rule has triggered" in checker
    assert "All rules may be untriggered" in checker
    assert "Do not modify repository files" in checker
    assert "You may write /tmp/repo_checker.json" in checker
    assert "Level" not in checker


def test_evidence_review_keeps_rendered_output_and_retry_contract():
    current = yaml.safe_load(PROMPTS.read_text())
    previous = yaml.safe_load(FROZEN.read_text())
    inputs = dict(issue="Frozen task", plan="# Plan\nApproach", checker_visible_playbook="Rule 1. The Plan is a placeholder.", retry_feedback="Invalid rule count")
    rendered = _render(current["checker_instance"], **inputs)
    assert rendered == _render(previous["checker_instance"], **inputs)
    assert "Invalid rule count" in rendered
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in rendered


def test_completed_smoke_remains_bound_to_frozen_v7():
    config = yaml.safe_load(Path("configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v3_20260927.yaml").read_text())
    assert config["inputs"]["prompt_bundle"] == str(FROZEN)
