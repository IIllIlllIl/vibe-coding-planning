#!/usr/bin/env python3
"""Freeze an exact recovery run from operationally incomplete PCE units."""

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


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _classify(row: dict[str, Any]) -> str:
    last = row.get("last_worker_output")
    if isinstance(last, dict) and "Disk quota exceeded" in str(last.get("error", "")):
        return "disk_quota_exceeded"
    if last is None and row.get("attempts_exhausted"):
        return "attempts_exhausted_without_worker_output"
    return "other_operational_incomplete"


def freeze(
    *,
    parent_selection_path: Path,
    parent_images_path: Path,
    parent_execution_path: Path,
    outcomes_path: Path,
    controller_status_path: Path,
    source_run_root: str,
    output_dir: Path,
    recovery_id: str,
) -> dict[str, Any]:
    output_names = ("selection.json", "images.json", "execution.json", "audit.json")
    existing = [output_dir / name for name in output_names if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite frozen recovery: {existing}")

    parent_selection = _read(parent_selection_path)
    parent_images = _read(parent_images_path)
    parent_execution = _read(parent_execution_path)
    controller_status = _read(controller_status_path)
    outcomes = [
        json.loads(line)
        for line in outcomes_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    units = parent_execution.get("execution_units")
    if not isinstance(units, list) or len(units) != len(outcomes):
        raise ValueError("parent execution and outcome row counts differ")
    if controller_status.get("execution_units") != len(outcomes):
        raise ValueError("controller status and outcome row counts differ")

    incomplete: list[tuple[dict[str, Any], dict[str, Any]]] = []
    completed = 0
    seen_indices: set[int] = set()
    for row in outcomes:
        task_index = int(row["task_index"])
        if task_index in seen_indices or not 0 <= task_index < len(units):
            raise ValueError(f"invalid or duplicate task index: {task_index}")
        seen_indices.add(task_index)
        unit = dict(units[task_index])
        if str(row["instance_id"]) != str(unit["instance_id"]):
            raise ValueError(f"outcome identity mismatch at task {task_index}")
        if row.get("status") == "completed":
            completed += 1
        elif row.get("status") == "incomplete":
            incomplete.append((row, unit))
        else:
            raise ValueError(f"unexpected outcome status at task {task_index}")
    if seen_indices != set(range(len(units))):
        raise ValueError("outcomes do not cover the complete parent execution")
    if completed != controller_status.get("completed_instances"):
        raise ValueError("completed count differs from controller authority")
    if len(incomplete) != controller_status.get("incomplete_instances"):
        raise ValueError("incomplete count differs from controller authority")

    incomplete_ids = {str(row["instance_id"]) for row, _ in incomplete}
    selected_ids = [
        str(value)
        for value in parent_selection["selected_instance_ids"]
        if str(value) in incomplete_ids
    ]
    parent_cases = {
        str(row["instance_id"]): row for row in parent_selection["selected_cases"]
    }
    selected_cases = [parent_cases[instance_id] for instance_id in selected_ids]
    selection = {
        "schema_version": 1,
        "selection_id": recovery_id,
        "purpose": "retry only operationally incomplete target-four PCE units",
        "dataset": parent_selection["dataset"],
        "dataset_revision": parent_selection["dataset_revision"],
        "source_manifest_sha256": parent_selection["source_manifest_sha256"],
        "source_instances_sha256": parent_selection["source_instances_sha256"],
        "source_instance_count": parent_selection["source_instance_count"],
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": selected_cases,
        "selected_count": len(selected_ids),
        "selection_policy": {
            "source": "operationally incomplete units in frozen parent execution",
            "completed_parent_units_excluded": True,
            "scientific_outcomes_do_not_select_recovery": True,
            "operational_incomplete_is_not_unresolved": True,
        },
    }
    selection_text = json.dumps(selection, indent=2, sort_keys=True) + "\n"
    selection_sha256 = hashlib.sha256(selection_text.encode()).hexdigest()

    selected_set = set(selected_ids)
    records = {
        key: value
        for key, value in parent_images["records"].items()
        if str(value["instance_id"]) in selected_set
    }
    if {str(value["instance_id"]) for value in records.values()} != selected_set:
        raise ValueError("parent images do not cover recovery selection")
    images = {
        "schema_version": 1,
        "purpose": "exact SIF projection for target-four incomplete recovery",
        "source_manifest_sha256": selection["source_manifest_sha256"],
        "selection_manifest_sha256": selection_sha256,
        "parent_image_manifest": str(parent_images_path),
        "parent_image_manifest_sha256": _sha256(parent_images_path),
        "projection_policy": "copy exact audited parent records",
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

    recovery_units = []
    source_map = []
    for recovery_index, (row, unit) in enumerate(incomplete):
        source_index = int(row["task_index"])
        recovery_units.append(
            {
                "execution_unit_index": recovery_index,
                "instance_id": str(unit["instance_id"]),
                "prior_usable_pce_count": int(unit["prior_usable_pce_count"]),
                "requested_unit_ordinal": int(unit["requested_unit_ordinal"]),
                "target_pce_count": int(unit["target_pce_count"]),
                "source_execution_unit_index": source_index,
            }
        )
        source_map.append(
            {
                "recovery_execution_unit_index": recovery_index,
                "source_execution_unit_index": source_index,
                "source_pce_run_index": int(row.get("pce_run_index", 1)),
                "instance_id": str(row["instance_id"]),
                "failure_class": _classify(row),
            }
        )
    execution = {
        "schema_version": 1,
        "purpose": "exact operational recovery of incomplete target-four units",
        "selection_manifest_sha256": selection_sha256,
        "target_pce_observations_per_instance": 4,
        "execution_unit_count": len(recovery_units),
        "execution_units": recovery_units,
    }
    execution["manifest_id"] = _stable_hash(execution)

    failure_counts = Counter(item["failure_class"] for item in source_map)
    task_counts = Counter(item["instance_id"] for item in source_map)
    no_output_slurm_states = Counter(
        str((row.get("last_slurm_status") or {}).get("state", "missing"))
        for row, _ in incomplete
        if row.get("last_worker_output") is None
    )
    audit = {
        "schema_version": 1,
        "audit_id": recovery_id,
        "parent_execution_units": len(outcomes),
        "parent_completed_units": completed,
        "parent_incomplete_units": len(incomplete),
        "recovery_task_count": len(selected_ids),
        "recovery_execution_units": len(recovery_units),
        "failure_class_counts": dict(sorted(failure_counts.items())),
        "no_worker_output_slurm_state_counts": dict(
            sorted(no_output_slurm_states.items())
        ),
        "incomplete_units_per_task_distribution": dict(
            sorted(Counter(task_counts.values()).items())
        ),
        "source_authorities": {
            "remote_cluster": "aion",
            "remote_run_root": source_run_root,
            "remote_raw_outcomes": f"{source_run_root}/raw_pce_outcomes.jsonl",
            "remote_controller_status": f"{source_run_root}/controller_status.json",
            "parent_selection": str(parent_selection_path),
            "parent_selection_sha256": _sha256(parent_selection_path),
            "parent_images": str(parent_images_path),
            "parent_images_sha256": _sha256(parent_images_path),
            "parent_execution": str(parent_execution_path),
            "parent_execution_sha256": _sha256(parent_execution_path),
            "raw_outcomes": str(outcomes_path),
            "raw_outcomes_sha256": _sha256(outcomes_path),
            "controller_status": str(controller_status_path),
            "controller_status_sha256": _sha256(controller_status_path),
        },
        "recovery_map": source_map,
        "retention": {
            "preserved": [
                "outcomes",
                "checkpoints",
                "trajectories",
                "failure records",
                "Slurm logs",
                "SIFs",
                "repository-history bundles",
            ],
            "reclaimed": "only exact disposable attempt_*/workspaces trees",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "selection.json").write_text(selection_text, encoding="utf-8")
    (output_dir / "images.json").write_text(
        json.dumps(images, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "execution.json").write_text(
        json.dumps(execution, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--parent-selection", required=True, type=Path)
    parser.add_argument("--parent-images", required=True, type=Path)
    parser.add_argument("--parent-execution", required=True, type=Path)
    parser.add_argument("--outcomes", required=True, type=Path)
    parser.add_argument("--controller-status", required=True, type=Path)
    parser.add_argument("--source-run-root", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--recovery-id", required=True)
    args = parser.parse_args()
    audit = freeze(
        parent_selection_path=args.parent_selection,
        parent_images_path=args.parent_images,
        parent_execution_path=args.parent_execution,
        outcomes_path=args.outcomes,
        controller_status_path=args.controller_status,
        source_run_root=args.source_run_root,
        output_dir=args.output_dir,
        recovery_id=args.recovery_id,
    )
    print(json.dumps({key: audit[key] for key in (
        "parent_completed_units",
        "parent_incomplete_units",
        "recovery_task_count",
        "recovery_execution_units",
        "failure_class_counts",
    )}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
