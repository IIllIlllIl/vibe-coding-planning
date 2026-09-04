"""Build a frozen Behavioral GEPA snapshot from separately frozen inputs."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from src.optimization.behavioral_models import TASK_SEMANTICS


CHECKER_MEDIA_PROJECTION = "omit-base64-media-preserve-descriptor-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_sha256(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mirror_relpath(repo_id: str) -> str:
    owner, name = repo_id.split("/", 1)
    return f"{owner}/{name}.git"


def _project_checker_media(value: Any, stats: dict[str, int]) -> Any:
    """Replace embedded base64 images with deterministic text-visible metadata."""
    if isinstance(value, list):
        return [_project_checker_media(item, stats) for item in value]
    if not isinstance(value, dict):
        return value
    source = value.get("source")
    if (
        value.get("type") == "image"
        and isinstance(source, dict)
        and source.get("type") == "base64"
        and isinstance(source.get("data"), str)
    ):
        encoded = source["data"]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Checker-visible image contains invalid base64") from exc
        stats["payloads_omitted"] += 1
        stats["encoded_characters_omitted"] += len(encoded)
        stats["decoded_bytes_preserved_by_hash"] += len(decoded)
        projected_source = {key: item for key, item in source.items() if key != "data"}
        projected_source["data_projection"] = {
            "status": "omitted_from_checker_text",
            "encoded_characters": len(encoded),
            "decoded_bytes": len(decoded),
            "decoded_sha256": hashlib.sha256(decoded).hexdigest(),
            "raw_authority": "frozen_stage2_case",
        }
        return {
            key: (
                projected_source
                if key == "source"
                else _project_checker_media(item, stats)
            )
            for key, item in value.items()
        }
    return {
        key: _project_checker_media(item, stats) for key, item in value.items()
    }


def build_snapshot(
    *,
    stage2_manifest_path: Path,
    stage2_case_root: Path,
    repository_cleaning_path: Path,
    proxy_manifest_path: Path,
    split_manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"snapshot output already exists: {output_dir}")
    stage2 = _load(stage2_manifest_path)
    cleaning = _load(repository_cleaning_path)
    proxies = _load(proxy_manifest_path)
    split = _load(split_manifest_path)
    if split.get("content_sha256") != _content_sha256(split):
        raise ValueError("split manifest content hash mismatch")
    if not split.get("complete") or split.get("provisional"):
        raise ValueError("split manifest must be complete and non-provisional")

    excluded = {item["case_id"] for item in cleaning["excluded_cases"]}
    eligible = {
        item["case_id"]: item
        for item in stage2["cases"]
        if item["status"] == "eligible" and item["case_id"] not in excluded
    }
    proxy_by_id = {item["case_id"]: item for item in proxies["cases"]}
    assignments = {item["case_id"]: item for item in split.get("assignments", [])}
    if set(proxy_by_id) != set(eligible):
        raise ValueError("temporal proxy cases do not match repository-ready cases")
    case_universe = split.get("case_universe", "all_repository_ready")
    if case_universe == "all_repository_ready":
        if set(assignments) != set(eligible):
            raise ValueError("split assignments do not match repository-ready cases")
    elif case_universe == "explicit_subset":
        if not assignments or not set(assignments) <= set(eligible):
            raise ValueError("split subset contains unavailable cases")
    else:
        raise ValueError(f"unsupported split case_universe: {case_universe!r}")
    if len(assignments) != len(split.get("assignments", [])):
        raise ValueError("split assignments contain duplicate case IDs")

    rows: dict[str, list[dict[str, Any]]] = {"train": [], "validation": []}
    projection_totals = {
        "affected_cases": 0,
        "payloads_omitted": 0,
        "encoded_characters_omitted": 0,
        "decoded_bytes_preserved_by_hash": 0,
    }
    for case_id in sorted(assignments):
        assignment = assignments[case_id]
        split_name = assignment.get("split")
        if split_name not in rows:
            raise ValueError(f"{case_id}: invalid split {split_name!r}")
        stage2_entry = eligible[case_id]
        case_path = stage2_case_root / stage2_entry["case_path"]
        if _sha256(case_path) != stage2_entry["case_sha256"]:
            raise ValueError(f"{case_id}: Stage-2 case hash mismatch")
        source = _load(case_path)
        if source["case_id"] != case_id:
            raise ValueError(f"{case_id}: Stage-2 case identity mismatch")
        behavior_signal = source["reflection_only"]["behavior_signal"]
        if behavior_signal == "explicit_approval":
            decision = "ACCEPT"
        elif behavior_signal == "explicit_rejection":
            decision = "DO_NOT_ACCEPT"
        else:
            raise ValueError(f"{case_id}: unsupported high-confidence signal")
        proxy = proxy_by_id[case_id]
        repo_id = str(source["selection_provenance"]["repo_id"])
        if proxy["repo_id"] != repo_id:
            raise ValueError(f"{case_id}: repository identity mismatch")
        case_projection = {
            "payloads_omitted": 0,
            "encoded_characters_omitted": 0,
            "decoded_bytes_preserved_by_hash": 0,
        }
        checker_events = _project_checker_media(
            source["checker_visible"]["events"], case_projection
        )
        if case_projection["payloads_omitted"]:
            projection_totals["affected_cases"] += 1
        for key in case_projection:
            projection_totals[key] += case_projection[key]

        rows[split_name].append(
            {
                "instance_id": case_id,
                "split": split_name,
                "task_semantics": TASK_SEMANTICS,
                "checker_input": {
                    "pre_p1_context": checker_events,
                    "proposed_plan_p1": source["checker_visible"]["proposed_plan"],
                    "repository_proxy": {
                        "repo": repo_id,
                        "proxy_commit": proxy["proxy_commit"],
                        "instance_id": case_id,
                        "state_semantics": proxy["repository_state_semantics"],
                        "conflict_authority": "pre_p1_observed_tool_results",
                    },
                },
                "supervision": {
                    "decision": decision,
                    "confidence": "high",
                    "signal": behavior_signal,
                },
                "reflection_evidence": source["reflection_only"],
                "audit_provenance": {
                    "mirror_relpath": _mirror_relpath(repo_id),
                    "proxy_source": proxy["proxy_source"],
                    "recorded_branch_ref_available": proxy[
                        "recorded_branch_ref_available"
                    ],
                    "time_gap_seconds": proxy["time_gap_seconds"],
                    "repository_state_semantics": proxy["repository_state_semantics"],
                    "stage2_case_sha256": stage2_entry["case_sha256"],
                    "dedup_group": assignment.get("dedup_group"),
                    "checker_media_projection": {
                        "policy": CHECKER_MEDIA_PROJECTION,
                        **case_projection,
                    },
                },
            }
        )

    output_dir.mkdir(parents=True)
    for split_name in ("train", "validation"):
        (output_dir / f"{split_name}.jsonl").write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows[split_name]
            ),
            encoding="utf-8",
        )
    manifest = {
        "schema_version": 1,
        "complete": True,
        "provisional": False,
        "task_semantics": TASK_SEMANTICS,
        "train_instances": len(rows["train"]),
        "validation_instances": len(rows["validation"]),
        "stage2_manifest_sha256": stage2["content_sha256"],
        "repository_cleaning_manifest_sha256": cleaning["content_sha256"],
        "temporal_proxy_manifest_sha256": proxies["content_sha256"],
        "split_manifest_sha256": split["content_sha256"],
        "case_universe": case_universe,
        "source_case_hashes_verified": True,
        "checker_media_projection": {
            "policy": CHECKER_MEDIA_PROJECTION,
            "scope": "checker_visible_pre_p1_context_only",
            "raw_stage2_cases_unchanged": True,
            **projection_totals,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage2-manifest", required=True, type=Path)
    parser.add_argument("--stage2-case-root", required=True, type=Path)
    parser.add_argument("--repository-cleaning", required=True, type=Path)
    parser.add_argument("--proxy-manifest", required=True, type=Path)
    parser.add_argument("--split-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    build_snapshot(
        stage2_manifest_path=args.stage2_manifest,
        stage2_case_root=args.stage2_case_root,
        repository_cleaning_path=args.repository_cleaning,
        proxy_manifest_path=args.proxy_manifest,
        split_manifest_path=args.split_manifest,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
