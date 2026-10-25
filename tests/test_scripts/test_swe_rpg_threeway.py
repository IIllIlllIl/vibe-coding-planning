"""Frozen splits and exact source subsets; no model, container or scheduler."""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.grouped_partition import partition
from scripts.tools.freeze_swe_rpg_threeway import assignments_for
from scripts.tools.freeze_swe_chat_behavioral_formal_split import _content_sha256
from src.swe_rpg_pce.dataset import prepare_subset, load_swe_rpg_pce_cases, EVALUATOR_FILE
from tests.test_swe_rpg_pce import rpg_source


def test_shared_partition_keeps_legacy_swe_chat_assignments():
    frozen = json.loads(Path("configs/frozen_swe_chat_behavioral_formal/swe-chat-hybrid-threeway-v1-20261010.json").read_text())
    expected = frozen["assignments"]
    actual = partition(expected, frozen["development_exposed_case_ids"], seed=42, test_target=24)
    assert actual == expected


def test_targeted_train_reserves_groups_and_ignores_input_order():
    rows = [{"case_id": str(i), "dedup_group": "g" + str(i // 2)} for i in range(20)]
    first = partition(rows, ["0"], train_target=12, test_target=4)
    assert first == partition(list(reversed(rows)), ["0"], train_target=12, test_target=4)
    assert {s: sum(a["split"] == s for a in first) for s in ("train", "validation", "test")} == {
        "train": 12, "validation": 4, "test": 4}
    grouped = defaultdict(set)
    for row in first:
        grouped[row["dedup_group"]].add(row["split"])
    assert all(len(splits) == 1 for splits in grouped.values())
    assert grouped["g0"] == {"train"}
    tiny = partition(rows[:6], ["0"], train_target=100, test_target=100)
    assert {row["split"] for row in tiny} == {"train", "validation", "test"}


def _case(identifier, repo, issue, base):
    return SimpleNamespace(instance_id=identifier, repo=repo, issue_description=issue,
                           base_commit=base, row_sha256=identifier)


def test_duplicate_components_use_only_issue_and_repository_identity():
    rows = [_case("a", "repo/a", "Identical issue body", "a"),
            _case("b", "repo/b", "  IDENTICAL issue BODY ", "b"),
            _case("c", "repo/c", "Unrelated report", "c"),
            _case("d", "repo/a", "Another report in same repository", "d")]
    first, links = assignments_for(rows)
    assert (first, links) == assignments_for(list(reversed(rows)))
    groups = {r["case_id"]: r["dedup_group"] for r in first}
    assert groups["a"] == groups["b"] == groups["d"] != groups["c"]
    assert links == [{"left": "a", "right": "b", "reason": "exact_issue"}]
    changed = deepcopy(rows)
    for row in changed:
        row.gold_patch = "changed future answer"
        row.outcome = "resolved"
    assert assignments_for(changed) == (first, links)


def test_source_subset_preserves_selected_line_and_evaluator_bytes(tmp_path):
    snapshot, images, _, _ = rpg_source(tmp_path)
    chosen = ["owner__repo-1", "owner__repo-2"]
    destination = tmp_path / "subset"
    before = {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=chosen, destination=destination)
    original_lines = (snapshot / "instances.jsonl").read_bytes().splitlines(keepends=True)
    assert (destination / "instances.jsonl").read_bytes() == b"".join(original_lines[:2])
    assert (destination / EVALUATOR_FILE).read_bytes() == (snapshot / EVALUATOR_FILE).read_bytes()
    cases, manifest, subset_images = load_swe_rpg_pce_cases(destination, destination / "images.json")
    assert {c.instance_id for c in cases} == set(chosen)
    assert {r["instance_id"] for r in subset_images["records"].values()} == set(chosen)
    assert manifest["subset_provenance"]["source_row_bytes_preserved"] is True
    assert before == {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=chosen, destination=destination)
    with pytest.raises(ValueError, match="different bytes"):
        prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=chosen[:1], destination=destination)


@pytest.mark.parametrize("identifiers", [[], ["missing"], ["owner__repo-1", "owner__repo-1"]])
def test_subset_rejects_invalid_selections_before_writing(tmp_path, identifiers):
    snapshot, images, _, _ = rpg_source(tmp_path)
    destination = tmp_path / "invalid"
    with pytest.raises(ValueError, match="subset IDs"):
        prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=identifiers, destination=destination)
    assert not destination.exists()


def test_subset_cannot_mutate_the_frozen_source_directory(tmp_path):
    snapshot, images, _, _ = rpg_source(tmp_path)
    before = {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}
    for target in (snapshot, snapshot / "nested"):
        with pytest.raises(ValueError, match="outside the frozen source"):
            prepare_subset(snapshot=snapshot, image_manifest=images,
                           instance_ids=["owner__repo-1"], destination=target)
    assert before == {str(p): p.read_bytes() for p in snapshot.rglob("*") if p.is_file()}


def test_frozen_rpg_coverage_atomic_groups_and_protected_exposure():
    root = Path("configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025")
    value = json.loads((root / "manifest.json").read_text())
    assert value["content_sha256"] == _content_sha256(value)
    assert len(value["assignments"]) == len({r["case_id"] for r in value["assignments"]}) == 78
    grouped, repos = defaultdict(set), defaultdict(set)
    for row in value["assignments"]:
        grouped[row["dedup_group"]].add(row["split"])
        repos[row["repo"]].add(row["split"])
        if row["case_id"] in value["development_exposed_case_ids"]:
            assert row["split"] == "train"
    assert all(len(s) == 1 for s in [*grouped.values(), *repos.values()])
    assert sum(row["split"] == "train" for row in value["assignments"]) == 47
    assert set(json.loads((root / "split-selection.json").read_text())) == {"train", "validation"}
    assert value["policy"]["assignment_outcome_independent"] is True
