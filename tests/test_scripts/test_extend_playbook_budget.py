from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.internal.extend_playbook_budget import extend


def _write(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_budget_extension_is_narrow_and_idempotent(tmp_path: Path) -> None:
    old = {"paths": {"run_dir": "same"}, "models": {"checker": "same"},
           "search": {"max_iterations": 12, "max_metric_calls": 1200},
           "budget": {"candidate_proposals": 12}}
    new = {**old, "search": {"max_iterations": 30, "max_metric_calls": 3000},
           "budget": {"candidate_proposals": 30},
           "resume": {"predecessor": "old.yaml"}}
    old_path, new_path = tmp_path / "old.yaml", tmp_path / "new.yaml"
    _write(old_path, old); _write(new_path, new)
    run_dir = tmp_path / "run"; run_dir.mkdir()
    old_hash = hashlib.sha256(old_path.read_bytes()).hexdigest()
    semantic = {"runtime_config": old_hash, "dataset": "frozen"}
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "semantic_config": semantic,
        "semantic_sha256": hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest(),
    }), encoding="utf-8")

    assert extend(run_dir, old_path, new_path)["status"] == "extended"
    assert extend(run_dir, old_path, new_path)["status"] == "already_extended"
    manifest = json.loads((run_dir / "run_manifest.json").read_text())
    assert manifest["semantic_config"]["dataset"] == "frozen"
    assert len(manifest["budget_extensions"]) == 1


def test_budget_extension_rejects_frozen_semantic_change(tmp_path: Path) -> None:
    old = {"models": {"checker": "a"}, "search": {"max_iterations": 12, "max_metric_calls": 1200}}
    new = {"models": {"checker": "b"}, "search": {"max_iterations": 30, "max_metric_calls": 3000}}
    old_path, new_path = tmp_path / "old.yaml", tmp_path / "new.yaml"
    _write(old_path, old); _write(new_path, new)
    with pytest.raises(ValueError, match="models.checker"):
        extend(tmp_path, old_path, new_path)
