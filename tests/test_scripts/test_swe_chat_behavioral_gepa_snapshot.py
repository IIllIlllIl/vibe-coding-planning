from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.tools.build_swe_chat_behavioral_gepa_snapshot import build_snapshot
from src.optimization.behavioral_dataset import load_behavioral_snapshot


def _write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(root: Path) -> dict[str, Path]:
    cases = root / "stage2-cases"
    case_entries = []
    for index, signal in enumerate(("explicit_approval", "explicit_rejection")):
        case_id = f"case-{index}"
        relative = f"cases/{case_id}.json"
        case = {
            "case_id": case_id,
            "selection_provenance": {"repo_id": "org/repo"},
            "checker_visible": {
                "events": [{"turn_type": "user_prompt", "content": case_id}],
                "proposed_plan": f"plan {case_id}",
            },
            "reflection_only": {
                "behavior_signal": signal,
                "decision_result": {"content": f"result {case_id}"},
                "subsequent_events": [],
                "later_plan_count": 0,
            },
        }
        case_hash = _write(cases / relative, case)
        case_entries.append(
            {
                "case_id": case_id,
                "status": "eligible",
                "case_path": relative,
                "case_sha256": case_hash,
            }
        )
    stage2 = root / "stage2.json"
    cleaning = root / "cleaning.json"
    proxies = root / "proxies.json"
    split = root / "split.json"
    _write(stage2, {"content_sha256": "stage2", "cases": case_entries})
    _write(
        cleaning,
        {"content_sha256": "cleaning", "excluded_cases": []},
    )
    _write(
        proxies,
        {
            "content_sha256": "proxy",
            "cases": [
                {
                    "case_id": entry["case_id"],
                    "repo_id": "org/repo",
                    "proxy_commit": str(index + 1) * 40,
                    "proxy_source": "recorded_branch",
                    "recorded_branch_ref_available": True,
                    "time_gap_seconds": index + 1,
                    "repository_state_semantics": "approximate_pre_session_proxy",
                }
                for index, entry in enumerate(case_entries)
            ],
        },
    )
    _write(
        split,
        {
            "complete": True,
            "provisional": False,
            "content_sha256": "split",
            "assignments": [
                {"case_id": "case-0", "split": "train", "dedup_group": "g0"},
                {
                    "case_id": "case-1",
                    "split": "validation",
                    "dedup_group": "g1",
                },
            ],
        },
    )
    return {
        "stage2_manifest_path": stage2,
        "stage2_case_root": cases,
        "repository_cleaning_path": cleaning,
        "proxy_manifest_path": proxies,
        "split_manifest_path": split,
    }


def test_snapshot_builder_joins_only_frozen_boundary_inputs(tmp_path):
    inputs = _inputs(tmp_path)
    output = tmp_path / "snapshot"

    manifest = build_snapshot(**inputs, output_dir=output)
    train, validation = load_behavioral_snapshot(output)

    assert manifest["train_instances"] == manifest["validation_instances"] == 1
    assert train[0].decision == "ACCEPT"
    assert validation[0].decision == "DO_NOT_ACCEPT"
    assert "result case-0" not in json.dumps(train[0].checker_payload())
    assert "result case-0" in json.dumps(train[0].reflection_evidence)
    assert train[0].audit_provenance["mirror_relpath"] == "org/repo.git"


def test_snapshot_builder_requires_complete_exact_split_universe(tmp_path):
    inputs = _inputs(tmp_path)
    split = json.loads(inputs["split_manifest_path"].read_text(encoding="utf-8"))
    split["assignments"].pop()
    _write(inputs["split_manifest_path"], split)

    with pytest.raises(ValueError, match="split assignments"):
        build_snapshot(**inputs, output_dir=tmp_path / "snapshot")
