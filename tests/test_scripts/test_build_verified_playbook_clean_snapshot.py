import hashlib
import json
from pathlib import Path

import yaml

from scripts.tools.build_verified_playbook_clean_snapshot import (
    _has_abrupt_ending,
    _plan_quality_reasons,
)


def test_plan_quality_exclusions_are_frozen_and_disjoint() -> None:
    assert _plan_quality_reasons("sympy__sympy-22080") == [
        "TRIVIAL_PLACEHOLDER_PLAN"
    ]
    assert _plan_quality_reasons("django__django-10097") == [
        "TRUNCATED_OR_STRUCTURALLY_INCOMPLETE_PLAN"
    ]
    assert _plan_quality_reasons("astropy__astropy-12907") == []


def test_abrupt_ending_ignores_trailing_markdown_but_not_missing_validation() -> None:
    assert _has_abrupt_ending("## Reproduction\nRun:") is True
    assert _has_abrupt_ending("## Patch\n**Current code:**") is True
    assert _has_abrupt_ending("## Patch\nBefore:`") is True
    assert _has_abrupt_ending("## Patch\nChange the return value.") is False
    assert _has_abrupt_ending("## Patch\nComplete change without validation") is False


def test_formal_playbook_run_freezes_clean375_and_three_reflections() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "configs/gepa_verified_reject_playbook_formal_12it_v1_20260910.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["status"] == "ready_not_launched"
    assert config["inputs"]["train_instance_ids"] is None
    assert config["inputs"]["validation_instance_ids"] is None
    assert config["search"] == {
        "max_iterations": 12,
        "reflection_minibatch_size": 8,
        "max_metric_calls": 1200,
        "seed": 42,
        "perfect_score": 0.0,
        "skip_perfect_score": True,
    }
    assert config["reflection"]["rounds"] == 3
    snapshot = root / config["paths"]["dataset_snapshot"]
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert (manifest["retained_instances"], manifest["train_instances"], manifest["validation_instances"]) == (
        375, 297, 78
    )
    for path_key, hash_key in (
        ("initial_playbook", "initial_playbook_sha256"),
        ("prompt_bundle", "prompt_bundle_sha256"),
    ):
        artifact = root / config["inputs"][path_key]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == config["inputs"][hash_key]
