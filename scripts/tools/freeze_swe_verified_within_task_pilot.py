#!/usr/bin/env python3
"""Freeze a reviewed within-task PCE pilot from existing authorities."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--formal-selection", required=True, type=Path)
    parser.add_argument("--clean-cases", required=True, type=Path)
    parser.add_argument("--parent-images", required=True, type=Path)
    parser.add_argument("--exclude-selection", required=True, type=Path)
    parser.add_argument("--fpta-report", action="append", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    selected_ids = [str(value) for value in spec["selected_instance_ids"]]
    if len(selected_ids) != len(set(selected_ids)) or not selected_ids:
        raise ValueError("pilot IDs must be nonempty and unique")

    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    formal = json.loads(args.formal_selection.read_text(encoding="utf-8"))
    clean = {
        row["instance_id"]: row
        for row in (
            json.loads(line)
            for line in args.clean_cases.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    formal_rows = {row["instance_id"]: row for row in formal["selected_cases"]}
    excluded = set(
        json.loads(args.exclude_selection.read_text(encoding="utf-8"))[
            "selected_instance_ids"
        ]
    )
    if set(selected_ids) & excluded:
        raise ValueError("pilot overlaps the excluded earlier pilot")
    if missing := set(selected_ids) - set(formal_rows):
        raise ValueError(f"pilot IDs missing from formal selection: {sorted(missing)}")
    if missing := set(selected_ids) - set(clean):
        raise ValueError(f"pilot IDs missing from clean cases: {sorted(missing)}")

    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.fpta_report]
    if len(reports) != 3:
        raise ValueError("exactly three FPTA reports are required")

    def state(report: dict[str, Any], instance_id: str) -> str:
        if instance_id in report["resolved_ids"]:
            return "R"
        if instance_id in report["unresolved_ids"]:
            return "U"
        return "X"

    selected_cases = []
    for instance_id in selected_ids:
        outcomes = "".join(state(report, instance_id) for report in reports)
        if set(outcomes) != {"R", "U"}:
            raise ValueError(f"{instance_id} is not FPTA mixed: {outcomes}")
        clean_row = clean[instance_id]
        formal_row = formal_rows[instance_id]
        selected_cases.append(
            {
                **formal_row,
                "existing_safe_pce_resolved": bool(clean_row["resolved"]),
                "fpta_deepseek_v3_outcomes": outcomes,
                "difficulty": str(clean_row["difficulty"]),
                "split": str(clean_row["split"]),
            }
        )

    selection = {
        "schema_version": 1,
        "selection_id": spec["selection_id"],
        "purpose": spec["purpose"],
        "dataset": source["dataset"],
        "dataset_revision": source["revision"],
        "source_manifest_sha256": _sha256(args.source_manifest),
        "source_instances_sha256": source["instances_sha256"],
        "source_instance_count": source["instances"],
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": selected_cases,
        "selected_count": len(selected_cases),
        "repository_distribution": dict(
            sorted(Counter(row["repo"] for row in selected_cases).items())
        ),
        "repeat_design": spec["repeat_design"],
        "selection_policy": spec["selection_policy"],
        "source_authorities": {
            "formal_selection": str(args.formal_selection),
            "formal_selection_sha256": _sha256(args.formal_selection),
            "clean_cases": str(args.clean_cases),
            "clean_cases_sha256": _sha256(args.clean_cases),
            "excluded_selection": str(args.exclude_selection),
            "excluded_selection_sha256": _sha256(args.exclude_selection),
            "fpta_reports": [
                {"path": str(path), "sha256": _sha256(path)}
                for path in args.fpta_report
            ],
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = args.output_dir / "selection.json"
    selection_path.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    parent_images = json.loads(args.parent_images.read_text(encoding="utf-8"))
    selected_set = set(selected_ids)
    records = {
        key: value
        for key, value in parent_images["records"].items()
        if value["instance_id"] in selected_set
    }
    if {value["instance_id"] for value in records.values()} != selected_set:
        raise ValueError("parent image manifest does not cover the pilot")
    if any(
        value["status"] != "audited" or not value["base_commit_verified"]
        for value in records.values()
    ):
        raise ValueError("pilot contains an unverified image")
    images = {
        "schema_version": 1,
        "purpose": "swe_verified_sif_subset_projection_for_within_task_pilot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": _sha256(args.source_manifest),
        "selection_manifest_sha256": _sha256(selection_path),
        "parent_image_manifest": str(args.parent_images),
        "parent_image_manifest_sha256": _sha256(args.parent_images),
        "projection_policy": "exact selected records copied without changing frozen SIF identity",
        "verify_base_commits": True,
        "records": records,
        "summary": {
            "records": len(records),
            "audited": len(records),
            "missing": 0,
            "base_commit_verified": len(records),
        },
    }
    images["manifest_id"] = _stable_hash(images)
    (args.output_dir / "images.json").write_text(
        json.dumps(images, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"selected": len(selected_ids), "output": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
