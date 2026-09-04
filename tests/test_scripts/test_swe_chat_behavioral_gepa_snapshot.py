from __future__ import annotations

import base64
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


def _split_hash(value: dict) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
                "events": [
                    {"turn_type": "user_prompt", "content": case_id},
                    *(
                        [
                            {
                                "turn_type": "tool_result",
                                "content": [
                                    {"type": "text", "text": "Screenshot captured."},
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": base64.b64encode(
                                                b"fixture-image"
                                            ).decode(),
                                        },
                                    },
                                ],
                            }
                        ]
                        if index == 0
                        else []
                    ),
                ],
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
    split_value = {
        "complete": True,
        "provisional": False,
        "case_universe": "all_repository_ready",
        "assignments": [
            {"case_id": "case-0", "split": "train", "dedup_group": "g0"},
            {
                "case_id": "case-1",
                "split": "validation",
                "dedup_group": "g1",
            },
        ],
    }
    split_value["content_sha256"] = _split_hash(split_value)
    _write(split, split_value)
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
    serialized_checker = json.dumps(train[0].checker_payload(), sort_keys=True)
    assert base64.b64encode(b"fixture-image").decode() not in serialized_checker
    descriptor = train[0].pre_p1_context[1]["content"][1]["source"][
        "data_projection"
    ]
    assert descriptor == {
        "status": "omitted_from_checker_text",
        "encoded_characters": 20,
        "decoded_bytes": 13,
        "decoded_sha256": hashlib.sha256(b"fixture-image").hexdigest(),
        "raw_authority": "frozen_stage2_case",
    }
    assert manifest["checker_media_projection"] == {
        "policy": "omit-base64-media-preserve-descriptor-v1",
        "scope": "checker_visible_pre_p1_context_only",
        "raw_stage2_cases_unchanged": True,
        "affected_cases": 1,
        "payloads_omitted": 1,
        "encoded_characters_omitted": 20,
        "decoded_bytes_preserved_by_hash": 13,
    }
    source_case = json.loads(
        (
            inputs["stage2_case_root"] / "cases/case-0.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        source_case["checker_visible"]["events"][1]["content"][1]["source"][
            "data"
        ]
        == base64.b64encode(b"fixture-image").decode()
    )


def test_snapshot_builder_requires_complete_exact_split_universe(tmp_path):
    inputs = _inputs(tmp_path)
    split = json.loads(inputs["split_manifest_path"].read_text(encoding="utf-8"))
    split["assignments"].pop()
    split["content_sha256"] = _split_hash(split)
    _write(inputs["split_manifest_path"], split)

    with pytest.raises(ValueError, match="split assignments"):
        build_snapshot(**inputs, output_dir=tmp_path / "snapshot")


def test_snapshot_builder_accepts_frozen_explicit_subset(tmp_path):
    inputs = _inputs(tmp_path)
    split = json.loads(inputs["split_manifest_path"].read_text(encoding="utf-8"))
    split["case_universe"] = "explicit_subset"
    split["assignments"] = split["assignments"][:1]
    split["content_sha256"] = _split_hash(split)
    _write(inputs["split_manifest_path"], split)

    manifest = build_snapshot(**inputs, output_dir=tmp_path / "snapshot")

    assert manifest["case_universe"] == "explicit_subset"
    assert manifest["train_instances"] == 1
    assert manifest["validation_instances"] == 0
