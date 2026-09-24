from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from src.optimization.playbook import (
    PlaybookBullet, RejectPlaybook, apply_curator_operations,
    validate_curator_proposal,
)


def test_retained_update_keeps_inactive_ancestry_across_later_proposals():
    original = RejectPlaybook((PlaybookBullet("plan-00001", "old rule"),))
    revised = apply_curator_operations(original, {
        "reasoning": "Clarify rule", "operations": [{
            "type": "UPDATE", "target_id": "plan-00001", "content": "new rule",
            "supporting_instance_ids": ["case"], "risk_analysis": "same scope",
        }],
    })
    validate_curator_proposal(original, revised)
    assert revised.bullets[0].lineage == ("plan-00001",)
    assert all(b.id != "plan-00001" for b in revised.bullets)
    assert validate_curator_proposal(revised, revised) == revised
    added = apply_curator_operations(revised, {
        "reasoning": "Independent concern", "operations": [{
            "type": "ADD", "content": "another rule",
            "supporting_instance_ids": ["case"], "risk_analysis": "narrow scope",
        }],
    })
    assert validate_curator_proposal(revised, added) == added


def test_historical_revise_and_delete_names_remain_replay_compatible():
    source = RejectPlaybook((
        PlaybookBullet("plan-00001", "old rule", helpful=2),
        PlaybookBullet("plan-00002", "remove me", harmful=1),
    ))
    result = apply_curator_operations(source, {
        "reasoning": "Replay a frozen operation vocabulary.",
        "operations": [
            {
                "type": "REVISE", "target_id": "plan-00001",
                "content": "new rule", "supporting_instance_ids": ["case"],
            },
            {
                "type": "DELETE", "target_id": "plan-00002",
                "supporting_instance_ids": ["case"],
            },
        ],
    })
    assert [(item.text, item.helpful, item.harmful, item.lineage)
            for item in result.bullets] == [
        ("new rule", 0, 0, ("plan-00001",)),
    ]


def test_remove_uses_the_new_curator_operation_name():
    source = RejectPlaybook((PlaybookBullet("plan-00001", "remove me"),))
    result = apply_curator_operations(source, {
        "reasoning": "The accumulated evidence no longer supports the rule.",
        "operations": [{
            "type": "REMOVE", "target_id": "plan-00001",
            "supporting_instance_ids": ["case"],
        }],
    })
    assert result.bullets == ()


@pytest.mark.parametrize("changes", [
    {"text": "changed"}, {"helpful": 2}, {"harmful": 2},
    {"category": "Scope"}, {"lineage": ()},
])
def test_retained_bullet_remains_immutable(changes):
    bullet = PlaybookBullet("plan-00002", "rule", lineage=("plan-00001",))
    with pytest.raises(ValueError, match="retained bullet ID"):
        validate_curator_proposal(RejectPlaybook((bullet,)),
                                 RejectPlaybook((replace(bullet, **changes),)))


def test_new_bullet_cannot_cite_inactive_historical_ancestor():
    source = RejectPlaybook((PlaybookBullet("plan-00002", "rule", lineage=("plan-00001",)),))
    with pytest.raises(ValueError, match="input bullet ID"):
        validate_curator_proposal(source, RejectPlaybook((
            PlaybookBullet("plan-00003", "new", lineage=("plan-00001",)),
        )))


@pytest.mark.parametrize("counter", ["helpful", "harmful"])
def test_new_bullet_cannot_manufacture_counts(counter):
    with pytest.raises(ValueError, match="zero counters"):
        validate_curator_proposal(RejectPlaybook(()), RejectPlaybook((
            PlaybookBullet("plan-00001", "rule", **{counter: 1}),
        )))


def test_recovery_preserves_experiment_and_imports_all_completed_agent_roles():
    prefix = "configs/gepa_verified_paired_levels_categorized_formal24_8it_"
    old = yaml.safe_load(Path(prefix + "v1_20260921.yaml").read_text())
    new = yaml.safe_load(Path(prefix + "v2_20260922.yaml").read_text())
    for key in old.keys() - {"run_id", "purpose", "paths", "hpc"}:
        assert new[key] == old[key], key
    assert {k: v for k, v in old["paths"].items() if k != "run_dir"} == {
        k: v for k, v in new["paths"].items() if k != "run_dir"}
    assert {k: v for k, v in old["hpc"].items() if k != "job_name_prefix"} == {
        k: v for k, v in new["hpc"].items() if k != "job_name_prefix"}
    assert new["paths"]["run_dir"] != old["paths"]["run_dir"]
    imported = new["checkpoint_import"]
    assert Path(imported["source_run_dir"]).is_absolute()
    assert imported["source_run_dir"].endswith(old["paths"]["run_dir"])
    assert set(imported["roles"]) == {"paired_repo_checker", "paired_repo_reflector", "curator"}
