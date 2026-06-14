"""Tests for the deterministic GEPA pilot subset builder."""

from __future__ import annotations

import json

from scripts.tools.build_gepa_pilot_dataset import build_pilot_dataset


def _record(instance_id: str, repo: str, resolved: bool, split: str) -> dict:
    return {
        "instance_id": instance_id,
        "repo": repo,
        "resolved": resolved,
        "split": split,
    }


def test_builds_balanced_reproducible_pilot_snapshot(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for split, per_label in (("train", 4), ("validation", 3)):
        records = []
        for resolved in (False, True):
            for index in range(per_label):
                records.append(
                    _record(
                        f"repo{index}__{split}-{resolved}-{index}",
                        f"org/repo{index}",
                        resolved,
                        split,
                    )
                )
        (source / f"{split}.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    first = build_pilot_dataset(source, tmp_path / "output", seed=42)
    second = build_pilot_dataset(source, tmp_path / "output", seed=42)

    assert first == second
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["train_instances"] == 6
    assert manifest["validation_instances"] == 4
    assert manifest["train_resolved"] == 3
    assert manifest["validation_resolved"] == 2
    assert len(set(manifest["train_instance_ids"])) == 6
