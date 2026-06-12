"""Tests for official PolyBench task-category derivation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.label_checker_task_categories import label_task_categories


class FakeLoader:
    def __init__(self, categories: dict[str, str]) -> None:
        self.categories = categories

    def load_instance(self, instance_id: str) -> dict[str, str]:
        return {"task_category": self.categories[instance_id]}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_labels_cases_and_preserves_source_snapshot(tmp_path):
    source = tmp_path / "snapshot" / "cases.jsonl"
    source.parent.mkdir()
    records = [
        {"instance_id": "repo__one", "resolved": True, "plan": "one"},
        {"instance_id": "repo__two", "resolved": False, "plan": "two"},
        {"instance_id": "repo__three", "resolved": False, "plan": "three"},
    ]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in records),
        encoding="utf-8",
    )
    source_hash = _sha256(source)

    manifest = label_task_categories(
        input_path=source,
        output_dir=source.parent / "derived" / "task_category_v1",
        loader=FakeLoader(
            {
                "repo__one": "Bug Fix",
                "repo__two": "Feature",
                "repo__three": "Bug Fix",
            }
        ),
    )

    assert _sha256(source) == source_hash
    assert manifest["source_cases_sha256"] == source_hash
    assert manifest["category_counts"] == {"Bug Fix": 2, "Feature": 1}
    labeled = [
        json.loads(line)
        for line in (
            source.parent
            / "derived"
            / "task_category_v1"
            / "cases.jsonl"
        ).read_text().splitlines()
    ]
    assert [row["task_category"] for row in labeled] == [
        "Bug Fix",
        "Feature",
        "Bug Fix",
    ]
    bug_fix = [
        json.loads(line)
        for line in (
            source.parent
            / "derived"
            / "task_category_v1"
            / "bug_fix_cases.jsonl"
        ).read_text().splitlines()
    ]
    assert [row["instance_id"] for row in bug_fix] == [
        "repo__one",
        "repo__three",
    ]


def test_missing_official_category_is_rejected(tmp_path):
    source = tmp_path / "cases.jsonl"
    source.write_text(
        json.dumps({"instance_id": "repo__one", "resolved": True}) + "\n",
        encoding="utf-8",
    )

    try:
        label_task_categories(
            input_path=source,
            output_dir=tmp_path / "derived",
            loader=FakeLoader({"repo__one": ""}),
        )
    except ValueError as exc:
        assert "task_category missing" in str(exc)
    else:
        raise AssertionError("missing category should fail")
