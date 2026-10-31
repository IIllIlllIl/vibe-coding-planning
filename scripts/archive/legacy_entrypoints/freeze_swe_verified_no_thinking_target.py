#!/usr/bin/env python3
"""Freeze a deficit-only no-thinking Safe-PCE run over clean411."""

from __future__ import annotations

import argparse
from collections import Counter
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--clean-cases", required=True, type=Path)
    parser.add_argument("--parent-selection", required=True, type=Path)
    parser.add_argument("--parent-images", required=True, type=Path)
    parser.add_argument("--completed-three-selection", required=True, type=Path)
    parser.add_argument("--completed-four-selection", required=True, type=Path)
    parser.add_argument(
        "--mixed-manifest", action="append", required=True, type=Path
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    generated_names = ("selection.json", "images.json", "census.json", "execution.json")
    existing_outputs = [
        args.output_dir / name
        for name in generated_names
        if (args.output_dir / name).exists()
    ]
    if existing_outputs:
        raise FileExistsError(f"refusing to overwrite frozen outputs: {existing_outputs}")

    spec = _read_json(args.spec)
    target = int(spec["target_reliable_pce_observations_per_instance"])
    if target != 4:
        raise ValueError("this frozen design requires exactly four observations")

    clean_rows = [
        json.loads(line)
        for line in args.clean_cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    clean_ids = [str(row["instance_id"]) for row in clean_rows]
    if len(clean_ids) != 411 or len(set(clean_ids)) != len(clean_ids):
        raise ValueError("clean authority must contain 411 unique tasks")

    parent = _read_json(args.parent_selection)
    parent_rows = {
        str(row["instance_id"]): row for row in parent["selected_cases"]
    }
    if missing := sorted(set(clean_ids) - set(parent_rows)):
        raise ValueError(f"clean tasks missing from parent selection: {missing}")

    completed_three_manifest = _read_json(args.completed_three_selection)
    completed_three = {
        str(value) for value in completed_three_manifest["selected_instance_ids"]
    }
    completed_four_manifest = _read_json(args.completed_four_selection)
    completed_four_attempted = {
        str(value) for value in completed_four_manifest["selected_instance_ids"]
    }
    if len(completed_three) != 116:
        raise ValueError("completed-three authority must contain 116 tasks")
    if len(completed_four_attempted) != 20:
        raise ValueError("completed-four authority must contain 20 tasks")
    if not completed_four_attempted <= completed_three:
        raise ValueError("fourth observations must belong to completed-three tasks")

    mixed_ids: set[str] = set()
    unreliable_observation_ids: set[str] = set()
    for path in args.mixed_manifest:
        manifest = _read_json(path)
        mixed_ids.update(str(case["instance_id"]) for case in manifest["cases"])
        unreliable_observation_ids.update(
            str(case["instance_id"])
            for case in manifest.get("excluded_cases", [])
        )
    if len(mixed_ids) != 7 or not mixed_ids <= set(clean_ids):
        raise ValueError("expected exactly seven known clean411 mixed-outcome tasks")
    if mixed_ids & unreliable_observation_ids:
        raise ValueError("a task cannot be both retained mixed evidence and noise")

    completed_four = completed_four_attempted - mixed_ids - unreliable_observation_ids
    if len(completed_four) != 17:
        raise ValueError("expected 17 reliable fourth observations")

    selected_ids = [value for value in clean_ids if value not in mixed_ids]
    selected_set = set(selected_ids)
    completed_three &= selected_set
    completed_four &= selected_set

    prior_counts = Counter({instance_id: 1 for instance_id in selected_ids})
    for instance_id in completed_three:
        prior_counts[instance_id] = 3
    for instance_id in completed_four:
        prior_counts[instance_id] = 4
    distribution = Counter(prior_counts.values())
    expected_distribution = {1: 295, 3: 92, 4: 17}
    if dict(distribution) != expected_distribution:
        raise ValueError(
            f"unexpected reliable-observation distribution: {dict(distribution)}"
        )

    selected_cases = [parent_rows[instance_id] for instance_id in selected_ids]
    selection = {
        "schema_version": 1,
        "selection_id": spec["selection_id"],
        "purpose": spec["purpose"],
        "dataset": parent["dataset"],
        "dataset_revision": parent["dataset_revision"],
        "source_manifest_sha256": parent["source_manifest_sha256"],
        "source_instances_sha256": parent["source_instances_sha256"],
        "source_instance_count": parent["source_instance_count"],
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": selected_cases,
        "selected_count": len(selected_ids),
        "repository_distribution": dict(
            sorted(Counter(row["repo"] for row in selected_cases).items())
        ),
        "selection_policy": {
            "universe": "safe_pce_clean411",
            "exclude_known_reliable_mixed_tasks": True,
            "excluded_mixed_task_count": len(mixed_ids),
            "target_reliable_pce_observations_per_instance": target,
            "historical_groups_are_not_runtime_groups": True,
            "operational_or_evaluator_noise_is_not_an_outcome": True,
            "probability_estimation_valid": False,
            "untouched_holdout": False,
        },
    }

    selection_text = json.dumps(selection, indent=2, sort_keys=True) + "\n"
    selection_sha256 = hashlib.sha256(selection_text.encode()).hexdigest()
    parent_images = _read_json(args.parent_images)
    image_records = {
        key: value
        for key, value in parent_images["records"].items()
        if str(value["instance_id"]) in selected_set
    }
    if {str(value["instance_id"]) for value in image_records.values()} != selected_set:
        raise ValueError("parent image manifest does not cover selected tasks")
    if any(
        value.get("status") != "audited"
        or value.get("base_commit_verified") is not True
        for value in image_records.values()
    ):
        raise ValueError("selection contains an unaudited image")
    images = {
        "schema_version": 1,
        "purpose": "verified SIF projection for clean411 non-mixed target-four run",
        "source_manifest_sha256": parent["source_manifest_sha256"],
        "selection_manifest_sha256": selection_sha256,
        "parent_image_manifest": str(args.parent_images),
        "parent_image_manifest_sha256": _sha256(args.parent_images),
        "projection_policy": "exact selected records copied without changing SIF identity",
        "verify_base_commits": True,
        "records": image_records,
        "summary": {
            "records": len(image_records),
            "audited": len(image_records),
            "missing": 0,
            "base_commit_verified": len(image_records),
        },
    }
    images["manifest_id"] = _stable_hash(images)

    census = {
        "schema_version": 1,
        "census_id": spec["census_id"],
        "purpose": "reliable task-level PCE count before target-four execution",
        "target_reliable_pce_observations_per_instance": target,
        "clean_task_count": len(clean_ids),
        "excluded_known_mixed_instance_ids": sorted(mixed_ids),
        "excluded_known_mixed_count": len(mixed_ids),
        "selected_non_mixed_task_count": len(selected_ids),
        "prior_reliable_pce_count_distribution": {
            str(key): value for key, value in sorted(distribution.items())
        },
        "unreliable_observation_instance_ids": sorted(unreliable_observation_ids),
        "unreliable_observations_count_toward_target": False,
        "authorities": {
            "spec": {"path": str(args.spec), "sha256": _sha256(args.spec)},
            "clean_cases": {
                "path": str(args.clean_cases),
                "sha256": _sha256(args.clean_cases),
            },
            "completed_three_selection": {
                "path": str(args.completed_three_selection),
                "sha256": _sha256(args.completed_three_selection),
            },
            "completed_four_selection": {
                "path": str(args.completed_four_selection),
                "sha256": _sha256(args.completed_four_selection),
            },
            "mixed_manifests": [
                {"path": str(path), "sha256": _sha256(path)}
                for path in args.mixed_manifest
            ],
            **spec["remote_outcome_authorities"],
        },
    }

    execution_units = []
    for instance_id in selected_ids:
        prior = prior_counts[instance_id]
        for ordinal in range(1, target - prior + 1):
            execution_units.append(
                {
                    "execution_unit_index": len(execution_units),
                    "instance_id": instance_id,
                    "prior_usable_pce_count": prior,
                    "requested_unit_ordinal": ordinal,
                    "target_pce_count": target,
                }
            )
    if len(execution_units) != 977:
        raise ValueError(f"expected 977 execution units, got {len(execution_units)}")
    execution = {
        "schema_version": 1,
        "purpose": "deficit-only no-thinking PCE execution to four reliable observations",
        "selection_manifest_sha256": selection_sha256,
        "target_pce_observations_per_instance": target,
        "prior_usable_pce_count_distribution": {
            str(key): value for key, value in sorted(distribution.items())
        },
        "instances_at_target_before_run": distribution[4],
        "instances_requiring_execution": len(selected_ids) - distribution[4],
        "execution_unit_count": len(execution_units),
        "execution_units": execution_units,
    }
    execution["manifest_id"] = _stable_hash(execution)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "selection.json").write_text(selection_text, encoding="utf-8")
    (args.output_dir / "images.json").write_text(
        json.dumps(images, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "census.json").write_text(
        json.dumps(census, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "execution.json").write_text(
        json.dumps(execution, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "selected_tasks": len(selected_ids),
                "excluded_mixed_tasks": len(mixed_ids),
                "execution_units": len(execution_units),
                "output": str(args.output_dir),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
