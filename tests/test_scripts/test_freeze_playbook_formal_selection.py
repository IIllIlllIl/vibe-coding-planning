import hashlib
import json
from pathlib import Path

import pytest

from scripts.tools.freeze_playbook_formal_selection import build


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    train = [
        {
            "instance_id": f"train-{index}",
            "split": "train",
            "repo": f"owner/repo-{index % 3}",
            "resolved": index % 4 != 0,
        }
        for index in range(20)
    ]
    validation = [
        {
            "instance_id": f"validation-{index}",
            "split": "validation",
            "repo": f"owner/repo-{index % 2}",
            "resolved": index % 3 != 0,
        }
        for index in range(10)
    ]
    _write_jsonl(root / "train.jsonl", train)
    _write_jsonl(root / "validation.jsonl", validation)
    (root / "manifest.json").write_text(json.dumps({
        "train_instances": 20,
        "validation_instances": 10,
        "artifacts": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("train.jsonl", "validation.jsonl")
        },
    }))
    return root


def test_formal_selection_is_exact_deterministic_and_exhaustive(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    build(
        snapshot=snapshot, output=first,
        train_count=16, validation_count=8, seed=42,
    )
    build(
        snapshot=snapshot, output=second,
        train_count=16, validation_count=8, seed=42,
    )
    assert first.read_bytes() == second.read_bytes()
    value = json.loads(first.read_text())
    assert value["selected_count"] == 24
    assert len(value["train_instance_ids"]) == 16
    assert len(value["validation_instance_ids"]) == 8
    assert len(value["excluded_from_formal_selection"]) == 6
    selected = set(value["train_instance_ids"] + value["validation_instance_ids"])
    excluded = {row["instance_id"] for row in value["excluded_from_formal_selection"]}
    assert not selected & excluded
    assert len(selected | excluded) == 30


def test_formal_selection_refuses_source_drift_and_overwrite(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    output = tmp_path / "formal.json"
    build(
        snapshot=snapshot, output=output,
        train_count=16, validation_count=8, seed=42,
    )
    with pytest.raises(FileExistsError):
        build(
            snapshot=snapshot, output=output,
            train_count=16, validation_count=8, seed=42,
        )
    output.unlink()
    (snapshot / "train.jsonl").write_text("drift\n")
    with pytest.raises(ValueError, match="source snapshot artifact drift"):
        build(
            snapshot=snapshot, output=output,
            train_count=16, validation_count=8, seed=42,
        )
