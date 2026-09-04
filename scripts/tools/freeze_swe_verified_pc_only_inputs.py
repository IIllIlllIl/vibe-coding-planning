#!/usr/bin/env python3
"""Freeze the lightweight inputs for paired SWE-Verified PC-only evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.evaluator.swe_evaluator import derive_image_name  # noqa: E402


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_frozen(path: Path, content: str) -> None:
    if path.is_file():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"refusing to change frozen artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def freeze_snapshot(source: Path, output: Path) -> dict[str, Any]:
    source_manifest_path = source / "manifest.json"
    source_cases_path = source / "cases.jsonl"
    source_exclusions_path = source / "exclusions.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if not source_manifest.get("complete") or source_manifest.get("provisional"):
        raise ValueError("source snapshot must be complete and non-provisional")
    if not source_manifest.get("immutable"):
        raise ValueError("source snapshot must be immutable")
    if file_sha256(source_cases_path) != source_manifest.get("cases_sha256"):
        raise ValueError("source cases hash differs from manifest")
    if file_sha256(source_exclusions_path) != source_manifest.get("exclusions_sha256"):
        raise ValueError("source exclusions hash differs from manifest")

    projected: list[dict[str, Any]] = []
    for raw in _read_jsonl(source_cases_path):
        checker_input = raw.get("checker_input")
        if not isinstance(checker_input, dict) or set(checker_input) != {
            "issue_description",
            "plan",
            "repository",
        }:
            raise ValueError(f"{raw.get('instance_id')}: invalid Checker boundary")
        repository = checker_input.get("repository")
        if not isinstance(repository, dict):
            raise ValueError(f"{raw.get('instance_id')}: repository must be a mapping")
        instance_id = str(raw["instance_id"])
        if repository.get("instance_id") != instance_id:
            raise ValueError(f"{instance_id}: repository identity mismatch")
        resolved = raw.get("resolved")
        if not isinstance(resolved, bool):
            raise ValueError(f"{instance_id}: resolved must be boolean")
        repository_projection = {
            "repo": str(repository["repo"]),
            "base_commit": str(repository["base_commit"]),
            "instance_id": instance_id,
            "dataset_type": "swebench",
        }
        repository_projection["image_name"] = derive_image_name(repository_projection)
        projected.append(
            {
                "schema_version": 1,
                "instance_id": instance_id,
                "split": str(raw["split"]),
                "resolved": resolved,
                "task_category": "",
                "language": "Python",
                "checker_input": {
                    "issue_description": str(checker_input["issue_description"]),
                    "plan": str(checker_input["plan"]),
                    "repository": repository_projection,
                },
            }
        )
    if len({row["instance_id"] for row in projected}) != len(projected):
        raise ValueError("source instance IDs must be unique")
    if len(projected) != int(source_manifest["selected_instances"]):
        raise ValueError("source selected-instance count mismatch")

    cases_content = "".join(_stable_json(row) + "\n" for row in projected)
    exclusions_content = "[]\n"
    cases_path = output / "cases.jsonl"
    exclusions_path = output / "exclusions.json"
    _write_frozen(cases_path, cases_content)
    _write_frozen(exclusions_path, exclusions_content)
    labels = Counter(row["resolved"] for row in projected)
    splits = Counter(row["split"] for row in projected)
    split_labels = {
        split: {
            "instances": sum(row["split"] == split for row in projected),
            "resolved": sum(row["split"] == split and row["resolved"] for row in projected),
            "unresolved": sum(
                row["split"] == split and not row["resolved"] for row in projected
            ),
        }
        for split in sorted(splits)
    }
    manifest = {
        "schema_version": 1,
        "purpose": "swe_verified_historical_plan_pc_only",
        "snapshot_id": output.name,
        "complete": True,
        "provisional": False,
        "immutable": True,
        "dataset": source_manifest["dataset"],
        "dataset_type": "swebench",
        "language": "Python",
        "case_file": "cases.jsonl",
        "case_file_sha256": file_sha256(cases_path),
        "exclusions_file": "exclusions.json",
        "exclusions_sha256": file_sha256(exclusions_path),
        "cases": {
            "instances": len(projected),
            "resolved": labels[True],
            "unresolved": labels[False],
            "splits": split_labels,
        },
        "projection_policy": "checker_visible_input_plus_historical_binary_label_v1",
        "contains_asi": False,
        "source": {
            "snapshot_id": source_manifest["snapshot_id"],
            "manifest_sha256": file_sha256(source_manifest_path),
            "cases_sha256": file_sha256(source_cases_path),
            "exclusions_sha256": file_sha256(source_exclusions_path),
            "cleaning_policy": source_manifest["cleaning_policy"],
            "excluded_instances": source_manifest["excluded_instances"],
        },
    }
    _write_frozen(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _guideline_source(source: Path) -> tuple[str, str, dict[str, Any]]:
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    filename = str(manifest["guideline_file"])
    text = (source / filename).read_text(encoding="utf-8")
    expected = manifest.get("guideline_sha256") or manifest.get("guideline_file_sha256")
    actual = hashlib.sha256(text.encode()).hexdigest()
    if actual != expected:
        raise ValueError(f"guideline hash differs from manifest: {source}")
    return text, actual, {
        "bundle_id": manifest["bundle_id"],
        "manifest_sha256": file_sha256(manifest_path),
        "guideline_sha256": actual,
    }


def freeze_guidelines(seed_source: Path, c4_source: Path, output: Path) -> dict[str, Any]:
    selected = []
    sources = {}
    for label, filename, source in (
        ("behavioral_neutral_seed", "behavioral_neutral_seed.md", seed_source),
        ("behavioral_c4", "behavioral_c4.md", c4_source),
    ):
        text, digest, provenance = _guideline_source(source)
        _write_frozen(output / filename, text)
        selected.append({"label": label, "path": filename, "guideline_sha256": digest})
        sources[label] = provenance
    manifest = {
        "schema_version": 1,
        "bundle_id": output.name,
        "complete": True,
        "immutable": True,
        "selected": selected,
        "sources": sources,
    }
    _write_frozen(output / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--output-snapshot", required=True, type=Path)
    parser.add_argument("--seed-source", required=True, type=Path)
    parser.add_argument("--c4-source", required=True, type=Path)
    parser.add_argument("--output-guidelines", required=True, type=Path)
    args = parser.parse_args()
    snapshot = freeze_snapshot(args.source_snapshot.resolve(), args.output_snapshot.resolve())
    guidelines = freeze_guidelines(
        args.seed_source.resolve(), args.c4_source.resolve(), args.output_guidelines.resolve()
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "instances": snapshot["cases"]["instances"],
                "guideline_bundle": guidelines["bundle_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
