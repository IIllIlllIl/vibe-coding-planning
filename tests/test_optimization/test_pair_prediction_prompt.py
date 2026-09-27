from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from src.optimization.paired_playbook import validate_ace_paired_reflector_review
from src.optimization.playbook import PlaybookBullet, RejectPlaybook
from src.optimization.playbook_hpc_agents import HPCPairedRepoPlaybookProposalAgents
from src.optimization.playbook_cli import _paired_review_validator, run_from_config


def review(key="failure_pattern"):
    return {
        "instance_id": "pair-1",
        "reasoning": "Compare the recorded approaches and Code behavior.",
        "error_identification": None,
        "root_cause_analysis": None,
        "correct_approach": None,
        "key_insights": [{key: "The Plan implements the opposite required behavior.",
                          "basis": "R preserves the requirement; U reverses it and Code retains that choice."}],
        "bullet_tags": [{"id": "plan-00001", "tag": "neutral", "attribution": None}],
    }


def seed():
    return RejectPlaybook((PlaybookBullet("plan-00001", "The Plan is a placeholder."),))


@pytest.mark.parametrize("contract,key", [("ace_v1", "concern"), ("ace_v2", "failure_pattern")])
def test_versioned_schema_preserves_input_and_allows_empty(contract, key):
    value = review(key)
    original = deepcopy(value)
    normalized = validate_ace_paired_reflector_review(
        value, instance_id="pair-1", playbook=seed(), output_contract=contract
    )
    assert value == original
    assert normalized["key_insights"] == value["key_insights"]
    value["key_insights"] = []
    assert validate_ace_paired_reflector_review(
        value, instance_id="pair-1", playbook=seed(), output_contract=contract
    )["key_insights"] == []


@pytest.mark.parametrize("contract,key", [("ace_v1", "failure_pattern"), ("ace_v2", "concern")])
def test_host_rejects_wrong_schema_without_alias_rewrite(contract, key):
    with pytest.raises(ValueError):
        validate_ace_paired_reflector_review(
            review(key), instance_id="pair-1", playbook=seed(), output_contract=contract
        )


def test_curator_index_preserves_pattern_basis_and_pair_analysis(tmp_path):
    class Executor:
        run_dir = tmp_path

        def run_wave(self, role, items):
            assert role == "curator"
            self.items = items
            return [{"agent_output": {"reasoning": "No change", "operations": []}}]

    executor = Executor()
    agents = HPCPairedRepoPlaybookProposalAgents(
        executor, image_records={}, maximum_tokens=2048, evidence_contract="ace_v2"
    )
    value = review()
    agents.curate(seed(), [value])
    root = Path(executor.items[0]["evidence_dir"])
    index = json.loads((root / "reflection_index.json").read_text())
    assert index[0]["key_insights"][0] == {"id": "pair-1:i1", **value["key_insights"][0]}
    for key in ("reasoning", "error_identification", "root_cause_analysis", "correct_approach", "bullet_tags"):
        assert index[0][key] == value[key]


def test_prompt_changes_only_learning_roles_and_uses_explicit_schema():
    directory = Path("configs/prompts")
    old = yaml.safe_load((directory / "offline_gepa_paired_binary_ace_codex_v8_evidence_review_20260927.yaml").read_text())
    new = yaml.safe_load((directory / "offline_gepa_paired_binary_ace_codex_v9_pair_prediction_20260927.yaml").read_text())
    for key in ("checker_system", "checker_instance"):
        assert new[key] == old[key]
    learning = "\n".join(new[key] for key in ("reflector_codex_system", "reflector_codex_instance", "curator_codex_system", "curator_codex_instance"))
    assert '"failure_pattern"' in learning
    assert '"basis"' in learning
    assert "concern" not in learning.lower()
    assert "perfect Plan" not in learning
    assert "64 tokens" in learning
    assert all(operation in learning for operation in ("ADD", "UPDATE", "MERGE", "REMOVE"))


def test_curator_readability_defines_reader_condition_and_language():
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v9_pair_prediction_20260927.yaml"
    ).read_text())
    curator = " ".join(prompts["curator_codex_system"].split())
    assert "developer who understands common software concepts" in curator
    assert "has not seen the training cases" in curator
    assert "what is wrong with the proposed approach and when that condition applies" in curator
    assert "Explain relationships directly" in curator
    assert "restate the condition in their own words" in curator
    assert "need not prescribe an investigation procedure" in curator


def test_cli_binds_new_validator():
    validate = _paired_review_validator({"reflection": {"output_contract": "ace_v2"}})
    assert "failure_pattern" in validate(review(), instance_id="pair-1", playbook=seed())["key_insights"][0]


@pytest.mark.parametrize("reflection,curation", [("ace_v2", "ace_v1"), ("legacy_v1", "ace_v2")])
def test_config_rejects_mixed_contracts_before_execution(tmp_path, reflection, curation):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "mode": "offline_paired_repo_concern_playbook",
        "reflection": {"output_contract": reflection},
        "curation": {"evidence_contract": curation},
    }))
    with pytest.raises(ValueError, match="versions must match"):
        run_from_config(path)
