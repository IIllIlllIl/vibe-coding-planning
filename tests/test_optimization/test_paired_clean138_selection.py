import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "configs/frozen_swe_verified_plan_pairs/20260919_safe_pce_within_task_pairs_v1"
SELECTION = ROOT / "configs/frozen_swe_verified_plan_pairs/20260922_operationally_clean138_v1/selection.json"
PREVIOUS = ROOT / "configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_20260921.yaml"


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_clean138_is_exact_operational_exclusion_without_resplitting():
    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    previous = yaml.safe_load(PREVIOUS.read_text(encoding="utf-8"))
    assert hashlib.sha256((SNAPSHOT / "manifest.json").read_bytes()).hexdigest() == selection["source_manifest_sha256"]
    old_ids = previous["inputs"]["train_instance_ids"]
    excluded = set(selection["excluded_from_previous_selection"])
    selected = selection["train_instance_ids"]
    assert len(old_ids) == 143
    assert len(excluded) == 5
    assert len(selected) == len(set(selected)) == 138
    assert selected == [pair_id for pair_id in old_ids if pair_id not in excluded]
    train = {row["pair_id"]: row for row in _rows(SNAPSHOT / "train.jsonl")}
    validation = _rows(SNAPSHOT / "validation.jsonl")
    assert set(selected).issubset(train)
    assert not ({train[pair_id]["task_id"] for pair_id in selected} & {row["task_id"] for row in validation})
    expected_observations = {
        "psf__requests-1921": "940dd5940d03630f42dc7db2e7b152886f63c765caee50066c95cc2c4476f016",
        "django__django-11749": "86acd6f811219d20e87650a4b8c112ea32a040260f26bc8cec8e5a3e43b7ed9a",
    }
    for pair_id in excluded:
        row = train[pair_id]
        assert row["unresolved_observation"]["observation_id"] == expected_observations[row["task_id"]]
    assert excluded == {
        pair_id for pair_id in old_ids
        if train[pair_id]["unresolved_observation"]["observation_id"]
        in set(expected_observations.values())
    }
