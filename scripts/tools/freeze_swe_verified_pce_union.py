#!/usr/bin/env python3
"""Freeze one flat SWE-Verified PCE selection from disjoint selections."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


def _file_sha256(path: Path) -> str:
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
    parser.add_argument("--selection", action="append", required=True, type=Path)
    parser.add_argument("--parent-images", required=True, type=Path)
    parser.add_argument("--completion-census", type=Path)
    parser.add_argument("--selection-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite frozen output: {args.output_dir}")

    selections = [
        json.loads(path.read_text(encoding="utf-8")) for path in args.selection
    ]
    if len(selections) < 2:
        raise ValueError("at least two source selections are required")
    source_hashes = {item["source_manifest_sha256"] for item in selections}
    source_instance_hashes = {
        item["source_instances_sha256"] for item in selections
    }
    source_instance_counts = {item["source_instance_count"] for item in selections}
    datasets = {(item["dataset"], item["dataset_revision"]) for item in selections}
    if (
        len(source_hashes) != 1
        or len(source_instance_hashes) != 1
        or len(source_instance_counts) != 1
        or len(datasets) != 1
    ):
        raise ValueError("source selections belong to different datasets")

    selected_ids: list[str] = []
    selected_cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, selection in zip(args.selection, selections, strict=True):
        ids = [str(value) for value in selection["selected_instance_ids"]]
        rows = list(selection["selected_cases"])
        if len(set(ids)) != len(ids) or selection.get("selected_count") != len(ids):
            raise ValueError(f"selection is not a unique frozen membership: {path}")
        if ids != [str(row["instance_id"]) for row in rows]:
            raise ValueError(f"selection order differs from selected_cases: {path}")
        overlap = seen & set(ids)
        if overlap:
            raise ValueError(f"source selections overlap: {sorted(overlap)}")
        seen.update(ids)
        selected_ids.extend(ids)
        selected_cases.extend(rows)

    dataset, revision = next(iter(datasets))
    selection_payload = {
        "schema_version": 1,
        "selection_id": args.selection_id,
        "purpose": "flat_safe_pce_execution_selection",
        "dataset": dataset,
        "dataset_revision": revision,
        "source_manifest_sha256": next(iter(source_hashes)),
        "source_instances_sha256": next(iter(source_instance_hashes)),
        "source_instance_count": next(iter(source_instance_counts)),
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": selected_cases,
        "selected_count": len(selected_ids),
        "repository_distribution": dict(
            sorted(Counter(row["repo"] for row in selected_cases).items())
        ),
        "selection_policy": {
            "execution_boundary": "one flat PCE selection",
            "source_groups_are_not_runtime_groups": True,
            "analysis_grouping_is_postprocessing_only": True,
        },
        "source_authorities": [
            {"path": str(path), "sha256": _file_sha256(path)}
            for path in args.selection
        ],
    }

    selection_text = json.dumps(selection_payload, indent=2, sort_keys=True) + "\n"
    selection_sha256 = hashlib.sha256(selection_text.encode("utf-8")).hexdigest()

    parent = json.loads(args.parent_images.read_text(encoding="utf-8"))
    if parent.get("source_manifest_sha256") != next(iter(source_hashes)):
        raise ValueError("parent image manifest belongs to another dataset")
    records = {
        key: value
        for key, value in parent["records"].items()
        if str(value["instance_id"]) in seen
    }
    if (
        len(records) != len(seen)
        or {str(value["instance_id"]) for value in records.values()} != seen
    ):
        raise ValueError("parent image manifest does not cover the union")
    if any(
        value.get("status") != "audited"
        or value.get("base_commit_verified") is not True
        for value in records.values()
    ):
        raise ValueError("union contains an unaudited image")

    images = {
        "schema_version": 1,
        "purpose": "swe_verified_sif_projection_for_flat_pce_selection",
        "source_manifest_sha256": next(iter(source_hashes)),
        "selection_manifest_sha256": selection_sha256,
        "parent_image_manifest": str(args.parent_images),
        "parent_image_manifest_sha256": _file_sha256(args.parent_images),
        "projection_policy": "copy exact selected records without changing SIF identity",
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
    result = {"selected": len(selected_ids), "output": str(args.output_dir)}
    execution = None
    if args.completion_census is not None:
        census = json.loads(args.completion_census.read_text(encoding="utf-8"))
        target = int(census["target_pce_observations_per_instance"])
        baseline = int(census["baseline"]["completed_observations_per_selected_instance"])
        if target < 1 or baseline < 0 or baseline > target:
            raise ValueError("completion census has invalid target or baseline")
        completed = Counter({instance_id: baseline for instance_id in selected_ids})
        slot_ids: set[str] = set()
        for slot in census["additional_completion_slots"]:
            slot_id = str(slot["slot_id"])
            if slot_id in slot_ids:
                raise ValueError(f"duplicate completion slot: {slot_id}")
            slot_ids.add(slot_id)
            slot_instances = [str(value) for value in slot["completed_instance_ids"]]
            if len(set(slot_instances)) != len(slot_instances):
                raise ValueError(f"completion slot contains duplicates: {slot_id}")
            unknown = set(slot_instances) - seen
            if unknown:
                raise ValueError(
                    f"completion slot contains instances outside selection: {sorted(unknown)}"
                )
            completed.update(slot_instances)
        if any(count > target for count in completed.values()):
            raise ValueError("completion census exceeds its target")

        execution_units = []
        for instance_id in selected_ids:
            prior = completed[instance_id]
            for requested_ordinal in range(1, target - prior + 1):
                execution_units.append(
                    {
                        "execution_unit_index": len(execution_units),
                        "instance_id": instance_id,
                        "prior_usable_pce_count": prior,
                        "requested_unit_ordinal": requested_ordinal,
                        "target_pce_count": target,
                    }
                )
        distribution = Counter(completed.values())
        execution = {
            "schema_version": 1,
            "purpose": "deficit_only_flat_pce_execution",
            "selection_manifest_sha256": selection_sha256,
            "completion_census": str(args.completion_census),
            "completion_census_sha256": _file_sha256(args.completion_census),
            "target_pce_observations_per_instance": target,
            "prior_usable_pce_count_distribution": {
                str(key): value for key, value in sorted(distribution.items())
            },
            "instances_at_target_before_run": sum(
                count == target for count in completed.values()
            ),
            "instances_requiring_execution": sum(
                count < target for count in completed.values()
            ),
            "execution_unit_count": len(execution_units),
            "execution_units": execution_units,
        }
        execution["manifest_id"] = _stable_hash(execution)
        result["execution_units"] = len(execution_units)

    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "selection.json").write_text(
        selection_text, encoding="utf-8"
    )
    (args.output_dir / "images.json").write_text(
        json.dumps(images, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if execution is not None:
        (args.output_dir / "execution.json").write_text(
            json.dumps(execution, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
