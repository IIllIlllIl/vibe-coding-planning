"""Tests for exact frozen Offline guideline bundles."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.tools.freeze_offline_guideline_bundle import freeze_guideline_bundle


def test_freeze_guidelines_preserves_exact_candidate_strings(tmp_path: Path) -> None:
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        json.dumps(
            [{"rules": text} for text in ("seed", "one", "two", "three", "four")]
        )
    )
    run_manifest = tmp_path / "run_manifest.json"
    run_manifest.write_text(json.dumps({"semantic_sha256": "semantic"}))
    progress = tmp_path / "progress.json"
    progress.write_text(
        json.dumps({"status": "completed", "iteration": 8, "best_candidate_idx": 2})
    )
    manifest = freeze_guideline_bundle(
        candidates_path=candidates,
        source_manifest_path=run_manifest,
        source_progress_path=progress,
        output_root=tmp_path / "bundles",
        source_run_id="run",
        source_artifact_root="iris:/run",
        created_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    root = tmp_path / "bundles" / manifest["bundle_id"]
    assert (root / "candidate_2.md").read_text() == "two"
    assert [item["source_candidate_index"] for item in manifest["selected"]] == [
        0,
        1,
        2,
        3,
    ]
    assert manifest["source_candidates_artifact"] == "iris:/run/candidates.json"
