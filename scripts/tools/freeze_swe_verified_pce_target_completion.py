#!/usr/bin/env python3
"""Freeze the exact PCE units needed to bring a task universe to a target count."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _validated_completed_counts(
    *,
    execution: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> tuple[Counter[str], dict[str, list[str]]]:
    units = execution.get("execution_units")
    if not isinstance(units, list) or len(units) != len(outcomes):
        raise ValueError("execution and outcome row counts differ")
    completed: Counter[str] = Counter()
    observed: dict[str, list[str]] = defaultdict(list)
    seen: set[int] = set()
    for row in outcomes:
        index = int(row["task_index"])
        if index in seen or not 0 <= index < len(units):
            raise ValueError(f"invalid or duplicate task index: {index}")
        seen.add(index)
        unit = units[index]
        instance_id = str(row["instance_id"])
        if instance_id != str(unit["instance_id"]):
            raise ValueError(f"outcome identity mismatch at task {index}")
        if row.get("status") != "completed":
            continue
        evaluator = row.get("evaluator_result")
        resolved = evaluator.get("evaluator_resolved") if isinstance(evaluator, dict) else None
        if not isinstance(resolved, bool):
            raise ValueError(f"completed row lacks evaluator outcome at task {index}")
        completed[instance_id] += 1
        observed[instance_id].append("R" if resolved else "U")
    if seen != set(range(len(units))):
        raise ValueError("outcomes do not cover the complete execution manifest")
    return completed, observed


def freeze(
    *,
    clean_cases_path: Path,
    all_selection_path: Path,
    all_images_path: Path,
    target_selection_path: Path,
    target_execution_path: Path,
    target_outcomes_path: Path,
    recovery_execution_path: Path,
    recovery_outcomes_path: Path,
    known_mixed_paths: list[Path],
    target_run_root: str,
    recovery_run_root: str,
    output_dir: Path,
    completion_id: str,
    target_count: int = 4,
) -> dict[str, Any]:
    output_names = ("selection.json", "images.json", "execution.json", "audit.json")
    existing = [output_dir / name for name in output_names if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite frozen completion: {existing}")

    clean_rows = _read_jsonl(clean_cases_path)
    clean_ids = [str(row["instance_id"]) for row in clean_rows]
    if len(clean_ids) != len(set(clean_ids)):
        raise ValueError("clean task universe contains duplicate instance IDs")
    clean_outcomes = {
        str(row["instance_id"]): "R" if bool(row["resolved"]) else "U"
        for row in clean_rows
    }

    known_sequences: dict[str, str] = {}
    for path in known_mixed_paths:
        manifest = _read(path)
        for case in manifest.get("cases", []):
            instance_id = str(case["instance_id"])
            sequence = str(case["outcome_sequence"])
            if instance_id in known_sequences:
                raise ValueError(f"duplicate known mixed task: {instance_id}")
            if not sequence or set(sequence) - {"R", "U"}:
                raise ValueError(f"invalid known outcome sequence: {instance_id}")
            known_sequences[instance_id] = sequence
    if not set(known_sequences) <= set(clean_ids):
        raise ValueError("known mixed tasks are outside the clean universe")

    target_selection = _read(target_selection_path)
    target_execution = _read(target_execution_path)
    target_outcomes = _read_jsonl(target_outcomes_path)
    recovery_execution = _read(recovery_execution_path)
    recovery_outcomes = _read_jsonl(recovery_outcomes_path)
    target_completed, target_observed = _validated_completed_counts(
        execution=target_execution,
        outcomes=target_outcomes,
    )
    recovery_completed, recovery_observed = _validated_completed_counts(
        execution=recovery_execution,
        outcomes=recovery_outcomes,
    )

    target_ids = {str(value) for value in target_selection["selected_instance_ids"]}
    if target_ids & set(known_sequences):
        raise ValueError("target execution unexpectedly includes known mixed tasks")
    if target_ids | set(known_sequences) != set(clean_ids):
        raise ValueError("target and known-mixed authorities do not cover clean universe")

    prior_counts = {instance_id: target_count for instance_id in target_ids}
    for unit in target_execution["execution_units"]:
        instance_id = str(unit["instance_id"])
        value = int(unit["prior_usable_pce_count"])
        if instance_id in prior_counts and prior_counts[instance_id] != target_count:
            if prior_counts[instance_id] != value:
                raise ValueError(f"inconsistent prior count: {instance_id}")
        prior_counts[instance_id] = value

    coverage: dict[str, int] = {}
    sequences: dict[str, str] = {}
    for instance_id in clean_ids:
        if instance_id in known_sequences:
            sequence = known_sequences[instance_id]
            coverage[instance_id] = len(sequence)
            sequences[instance_id] = sequence
            continue
        prior = prior_counts[instance_id]
        coverage[instance_id] = (
            prior + target_completed[instance_id] + recovery_completed[instance_id]
        )
        sequences[instance_id] = (
            clean_outcomes[instance_id] * prior
            + "".join(target_observed[instance_id])
            + "".join(recovery_observed[instance_id])
        )
        if len(sequences[instance_id]) != coverage[instance_id]:
            raise ValueError(f"coverage sequence mismatch: {instance_id}")
    if any(value > target_count for value in coverage.values()):
        raise ValueError("an input authority exceeds the requested target count")

    missing_ids = [instance_id for instance_id in clean_ids if coverage[instance_id] < target_count]
    all_selection = _read(all_selection_path)
    all_cases = {
        str(row["instance_id"]): row for row in all_selection["selected_cases"]
    }
    if not set(missing_ids) <= set(all_cases):
        raise ValueError("all-task selection does not cover completion tasks")
    selected_cases = [all_cases[instance_id] for instance_id in missing_ids]
    selection = {
        "schema_version": 1,
        "selection_id": completion_id,
        "purpose": "complete every clean task to four reliable PCE observations",
        "dataset": all_selection["dataset"],
        "dataset_revision": all_selection["dataset_revision"],
        "source_manifest_sha256": all_selection["source_manifest_sha256"],
        "source_instances_sha256": all_selection["source_instances_sha256"],
        "source_instance_count": all_selection["source_instance_count"],
        "selected_instance_ids": missing_ids,
        "selected_instance_ids_sha256": _stable_hash(missing_ids),
        "selected_cases": selected_cases,
        "selected_count": len(missing_ids),
        "selection_policy": {
            "universe": "frozen clean411 Safe PCE cases",
            "target_reliable_observations_per_task": target_count,
            "select_only_current_deficits": True,
            "operational_incomplete_is_not_unresolved": True,
        },
    }
    selection_text = json.dumps(selection, indent=2, sort_keys=True) + "\n"
    selection_sha256 = hashlib.sha256(selection_text.encode()).hexdigest()

    all_images = _read(all_images_path)
    records = {
        key: value
        for key, value in all_images["records"].items()
        if str(value["instance_id"]) in set(missing_ids)
    }
    if {str(value["instance_id"]) for value in records.values()} != set(missing_ids):
        raise ValueError("all-task image authority does not cover completion tasks")
    images = {
        "schema_version": 1,
        "purpose": "exact SIF projection for clean411 target-four completion",
        "source_manifest_sha256": selection["source_manifest_sha256"],
        "selection_manifest_sha256": selection_sha256,
        "parent_image_manifest": str(all_images_path),
        "parent_image_manifest_sha256": _sha256(all_images_path),
        "projection_policy": "copy exact audited formal482 records",
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

    units = []
    for instance_id in missing_ids:
        for ordinal in range(1, target_count - coverage[instance_id] + 1):
            units.append(
                {
                    "execution_unit_index": len(units),
                    "instance_id": instance_id,
                    "prior_usable_pce_count": coverage[instance_id],
                    "requested_unit_ordinal": ordinal,
                    "target_pce_count": target_count,
                }
            )
    execution = {
        "schema_version": 1,
        "purpose": "complete clean411 tasks to four reliable PCE observations",
        "selection_manifest_sha256": selection_sha256,
        "target_pce_observations_per_instance": target_count,
        "execution_unit_count": len(units),
        "execution_units": units,
    }
    execution["manifest_id"] = _stable_hash(execution)

    missing_mixed = [
        instance_id
        for instance_id in missing_ids
        if set(sequences[instance_id]) == {"R", "U"}
    ]
    audit = {
        "schema_version": 1,
        "audit_id": completion_id,
        "clean_task_count": len(clean_ids),
        "target_observations_per_task": target_count,
        "current_reliable_observations": sum(coverage.values()),
        "completion_task_count": len(missing_ids),
        "completion_execution_units": len(units),
        "coverage_distribution_before_completion": dict(
            sorted(Counter(coverage.values()).items())
        ),
        "deficit_units_per_task_distribution": dict(
            sorted(Counter(target_count - coverage[item] for item in missing_ids).items())
        ),
        "known_mixed_completion_tasks": missing_mixed,
        "known_mixed_completion_units": sum(
            target_count - coverage[item] for item in missing_mixed
        ),
        "non_mixed_completion_tasks": len(missing_ids) - len(missing_mixed),
        "non_mixed_completion_units": len(units)
        - sum(target_count - coverage[item] for item in missing_mixed),
        "task_coverage": [
            {
                "instance_id": instance_id,
                "reliable_observations": coverage[instance_id],
                "observed_sequence": sequences[instance_id],
                "requested_units": target_count - coverage[instance_id],
            }
            for instance_id in missing_ids
        ],
        "source_authorities": {
            "clean_cases": str(clean_cases_path),
            "clean_cases_sha256": _sha256(clean_cases_path),
            "all_selection": str(all_selection_path),
            "all_selection_sha256": _sha256(all_selection_path),
            "all_images": str(all_images_path),
            "all_images_sha256": _sha256(all_images_path),
            "target_selection": str(target_selection_path),
            "target_selection_sha256": _sha256(target_selection_path),
            "target_execution": str(target_execution_path),
            "target_execution_sha256": _sha256(target_execution_path),
            "target_outcomes": str(target_outcomes_path),
            "target_outcomes_sha256": _sha256(target_outcomes_path),
            "target_run_root": target_run_root,
            "recovery_execution": str(recovery_execution_path),
            "recovery_execution_sha256": _sha256(recovery_execution_path),
            "recovery_outcomes": str(recovery_outcomes_path),
            "recovery_outcomes_sha256": _sha256(recovery_outcomes_path),
            "recovery_run_root": recovery_run_root,
            "known_mixed_manifests": [
                {"path": str(path), "sha256": _sha256(path)}
                for path in known_mixed_paths
            ],
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
    parser.add_argument("--clean-cases", required=True, type=Path)
    parser.add_argument("--all-selection", required=True, type=Path)
    parser.add_argument("--all-images", required=True, type=Path)
    parser.add_argument("--target-selection", required=True, type=Path)
    parser.add_argument("--target-execution", required=True, type=Path)
    parser.add_argument("--target-outcomes", required=True, type=Path)
    parser.add_argument("--recovery-execution", required=True, type=Path)
    parser.add_argument("--recovery-outcomes", required=True, type=Path)
    parser.add_argument("--known-mixed", required=True, type=Path, action="append")
    parser.add_argument("--target-run-root", required=True)
    parser.add_argument("--recovery-run-root", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--completion-id", required=True)
    args = parser.parse_args()
    audit = freeze(
        clean_cases_path=args.clean_cases,
        all_selection_path=args.all_selection,
        all_images_path=args.all_images,
        target_selection_path=args.target_selection,
        target_execution_path=args.target_execution,
        target_outcomes_path=args.target_outcomes,
        recovery_execution_path=args.recovery_execution,
        recovery_outcomes_path=args.recovery_outcomes,
        known_mixed_paths=args.known_mixed,
        target_run_root=args.target_run_root,
        recovery_run_root=args.recovery_run_root,
        output_dir=args.output_dir,
        completion_id=args.completion_id,
    )
    print(
        json.dumps(
            {
                key: audit[key]
                for key in (
                    "current_reliable_observations",
                    "completion_task_count",
                    "completion_execution_units",
                    "coverage_distribution_before_completion",
                    "known_mixed_completion_tasks",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
