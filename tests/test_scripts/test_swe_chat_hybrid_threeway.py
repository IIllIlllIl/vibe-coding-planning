from collections import defaultdict
import json
from pathlib import Path

import pytest

from scripts.tools.freeze_swe_chat_hybrid_threeway import partition


def test_grouping_is_deterministic_and_preserves_exposed_components():
    assignments = [{"case_id": str(i), "dedup_group": f"g{i // 2}"} for i in range(12)]
    result = partition(assignments, ["0"], seed=42, test_target=4)
    assert result == partition(list(reversed(assignments)), ["0"], seed=42, test_target=4)
    groups = defaultdict(set)
    for item in result:
        groups[item["dedup_group"]].add(item["split"])
    assert all(len(value) == 1 for value in groups.values())
    assert groups["g0"] == {"train"}
    assert sum(row["split"] == "test" for row in result) == 4


def test_invalid_exposure_or_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        partition([{"case_id": "a", "dedup_group": "g"}], ["missing"])
    with pytest.raises(ValueError):
        partition([{"case_id": "a", "dedup_group": "g"}] * 2, ["a"])


def test_frozen_manifest_covers_131_with_separate_test():
    from scripts.tools.freeze_swe_chat_behavioral_formal_split import _content_sha256
    path = Path("configs/frozen_swe_chat_behavioral_formal/swe-chat-hybrid-threeway-v1-20261010.json")
    value = json.loads(path.read_text())
    assert value["content_sha256"] == _content_sha256(value)
    assert [value["counts"][s]["cases"] for s in ("train", "validation", "test")] == [84, 23, 24]
    assert len({a["case_id"] for a in value["assignments"]}) == 131
    assert value["historically_untouched"] is False
    groups = defaultdict(set)
    for a in value["assignments"]:
        groups[a["dedup_group"]].add(a["split"])
    assert all(len(v) == 1 for v in groups.values())
    assert all(a["split"] == "train" for a in value["assignments"]
               if a["case_id"] in value["development_exposed_case_ids"])
