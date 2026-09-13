#!/usr/bin/env python3
"""Freeze the first formal Safe PCE membership from the fixed Verified source."""

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

from src.swe_verified_pce.dataset import file_sha256  # noqa: E402


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _stable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def freeze_selection(
    *,
    source_snapshot: Path,
    historical_exclusions: Path,
    operational_exclusions: set[str],
    selection_id: str,
) -> dict[str, Any]:
    source_manifest_path = source_snapshot / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (
        source_manifest.get("dataset") != "SWE-bench/SWE-bench_Verified"
        or source_manifest.get("complete") is not True
        or source_manifest.get("provisional") is not False
        or int(source_manifest.get("instances", -1)) != 500
    ):
        raise ValueError(
            "formal Safe PCE requires the complete fixed Verified500 source"
        )
    rows_path = source_snapshot / str(source_manifest["instances_file"])
    if file_sha256(rows_path) != source_manifest.get("instances_sha256"):
        raise ValueError("source instances differ from their frozen hash")
    wrappers = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(wrappers) != 500:
        raise ValueError("formal Safe PCE source must contain exactly 500 rows")
    source_ids = [str(wrapper["instance_id"]) for wrapper in wrappers]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("formal Safe PCE source IDs must be unique")
    source_id_set = set(source_ids)

    exclusions = json.loads(historical_exclusions.read_text(encoding="utf-8"))
    if not isinstance(exclusions, list):
        raise ValueError("historical exclusions must be a JSON list")
    placeholder_rows = [
        row for row in exclusions if row.get("reason") == "resolved placeholder plan"
    ]
    placeholder_ids = {str(row["instance_id"]) for row in placeholder_rows}
    if len(placeholder_rows) != 17 or len(placeholder_ids) != 17:
        raise ValueError("expected exactly 17 resolved placeholder exclusions")
    if not placeholder_ids <= source_id_set:
        raise ValueError("placeholder exclusions contain unknown source IDs")
    if not operational_exclusions or not operational_exclusions <= source_id_set:
        raise ValueError("operational exclusions must be known nonempty source IDs")
    overlap = placeholder_ids & operational_exclusions
    if overlap:
        raise ValueError("scientific and operational exclusions must be disjoint")

    excluded = placeholder_ids | operational_exclusions
    selected_wrappers = [
        wrapper for wrapper in wrappers if str(wrapper["instance_id"]) not in excluded
    ]
    selected_ids = [str(wrapper["instance_id"]) for wrapper in selected_wrappers]
    if len(selected_ids) != 482:
        raise ValueError("formal Safe PCE selection must contain exactly 482 cases")

    repo_distribution = Counter(
        str(wrapper["source_row"]["repo"]) for wrapper in selected_wrappers
    )
    payload = {
        "schema_version": 1,
        "selection_id": selection_id,
        "purpose": "first_formal_safe_pce_raw_evidence_generation",
        "dataset": source_manifest["dataset"],
        "dataset_revision": source_manifest["revision"],
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "source_instances_sha256": file_sha256(rows_path),
        "source_instance_count": len(wrappers),
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": [
            {
                "instance_id": str(wrapper["instance_id"]),
                "repo": str(wrapper["source_row"]["repo"]),
                "row_sha256": str(wrapper["row_sha256"]),
            }
            for wrapper in selected_wrappers
        ],
        "selected_count": len(selected_ids),
        "repository_distribution": dict(sorted(repo_distribution.items())),
        "exclusions": {
            "scientific": {
                "policy": "historical_resolved_placeholder_or_effectively_absent_plan",
                "count": len(placeholder_ids),
                "instance_ids": sorted(placeholder_ids),
                "reason_code_distribution": dict(
                    sorted(
                        Counter(
                            str(row["reason_code"]) for row in placeholder_rows
                        ).items()
                    )
                ),
                "source_path": _stable_path(historical_exclusions),
                "source_sha256": file_sha256(historical_exclusions),
            },
            "operational": {
                "policy": "required_frozen_sif_absent_at_prelaunch_census",
                "count": len(operational_exclusions),
                "instance_ids": sorted(operational_exclusions),
            },
        },
        "selection_policy": {
            "order": "frozen_source_order",
            "uses_historical_outcome": True,
            "historical_outcome_use": "exclude_only_resolved_placeholder_or_absent_plan_cases",
            "uses_historical_plan_text_as_runtime_input": False,
            "uses_historical_code_or_evaluator_trajectory_as_runtime_input": False,
            "outcome_balanced": False,
            "untouched_holdout": False,
        },
        "launch_gate": {
            "required_image_records": 482,
            "all_sif_sha256_values_frozen": True,
            "all_declared_base_commits_verified": True,
        },
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--historical-exclusions", required=True, type=Path)
    parser.add_argument("--operational-exclusion", action="append", default=[])
    parser.add_argument("--selection-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    payload = freeze_selection(
        source_snapshot=args.source_snapshot,
        historical_exclusions=args.historical_exclusions,
        operational_exclusions=set(args.operational_exclusion),
        selection_id=args.selection_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "selection_id": payload["selection_id"],
                "selected": payload["selected_count"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
