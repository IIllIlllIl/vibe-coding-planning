from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.tools.extend_offline_iteration_target import (
    _semantic_sha256,
    extend_iteration_target,
)


def _completed_run(tmp_path: Path) -> tuple[Path, bytes]:
    semantic = {"search": {"max_iterations": 8}, "source": {"example.py": "abc"}}
    manifest = {
        "version": 1,
        "semantic_config": semantic,
        "semantic_sha256": _semantic_sha256(semantic),
        "initial_max_metric_calls": 1500,
        "latest_max_metric_calls": 1500,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (tmp_path / "run_manifest.json").write_bytes(manifest_bytes)
    (tmp_path / "gepa_state.bin").write_bytes(b"checkpoint")
    (tmp_path / "gepa_resume_state.json").write_text(
        json.dumps({"gepa_state_i": 7}), encoding="utf-8"
    )
    (tmp_path / "progress.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    return tmp_path, manifest_bytes


def test_extends_completed_offline_target_with_lineage(tmp_path: Path) -> None:
    run_dir, original = _completed_run(tmp_path)

    result = extend_iteration_target(
        run_dir,
        new_target=20,
        reason="continue the formal search",
    )

    updated = json.loads((run_dir / "run_manifest.json").read_text())
    assert result["from"] == 8
    assert result["to"] == 20
    assert updated["semantic_config"]["search"]["max_iterations"] == 20
    assert updated["initial_max_iterations"] == 8
    assert updated["latest_max_iterations"] == 20
    assert updated["semantic_sha256"] == _semantic_sha256(
        updated["semantic_config"]
    )
    extension = updated["iteration_target_extensions"][-1]
    assert extension["additional_iterations"] == 12
    assert extension["checkpoint_gepa_state_i"] == 7
    assert extension["checkpoint_sha256"] == hashlib.sha256(
        b"checkpoint"
    ).hexdigest()
    assert (
        run_dir / "run_manifest.before_iteration_extension_8.json"
    ).read_bytes() == original
    checkpoint = run_dir / "iteration_checkpoints" / "iteration_0008"
    assert (checkpoint / "result.json").read_text() == "{}"
    assert not (run_dir / "result.json").exists()


def test_rejects_extension_before_completed_target(tmp_path: Path) -> None:
    run_dir, _ = _completed_run(tmp_path)
    (run_dir / "progress.json").write_text(
        json.dumps({"status": "running"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="only after completion"):
        extend_iteration_target(run_dir, new_target=20, reason="invalid")
