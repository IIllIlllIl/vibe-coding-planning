#!/usr/bin/env python3
"""Freeze an outcome-independent, repository-covering SWE-Verified selection."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.swe_verified_pce.dataset import file_sha256  # noqa: E402


def _rank(seed: str, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()


def freeze_selection(
    *,
    source_snapshot: Path,
    selection_id: str,
    seed: str,
    size: int,
    excluded_instance_ids: set[str],
) -> dict[str, Any]:
    manifest_path = source_snapshot / "manifest.json"
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        source_manifest.get("dataset") != "SWE-bench/SWE-bench_Verified"
        or not source_manifest.get("revision")
        or source_manifest.get("complete") is not True
        or source_manifest.get("provisional") is not False
    ):
        raise ValueError("selection requires a complete fixed SWE-Verified snapshot")
    rows_path = source_snapshot / str(source_manifest["instances_file"])
    if file_sha256(rows_path) != source_manifest.get("instances_sha256"):
        raise ValueError("source instances differ from their frozen hash")
    wrappers = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = [wrapper["source_row"] for wrapper in wrappers]
    source_ids = {str(row["instance_id"]) for row in rows}
    if len(source_ids) != len(rows) or len(rows) != source_manifest.get("instances"):
        raise ValueError("source instance count or identity differs from its manifest")
    unknown_exclusions = excluded_instance_ids - source_ids
    if unknown_exclusions:
        raise ValueError(
            "excluded IDs are absent from source: "
            + ", ".join(sorted(unknown_exclusions))
        )
    eligible = [
        row for row in rows if str(row["instance_id"]) not in excluded_instance_ids
    ]
    if size <= 0 or size > len(eligible):
        raise ValueError("selection size must fit the eligible source universe")

    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_repo[str(row["repo"])].append(row)
    if size < len(by_repo):
        raise ValueError("selection size is too small to cover every repository")

    representatives = [
        min(
            by_repo[repo],
            key=lambda row: (_rank(seed, str(row["instance_id"])), row["instance_id"]),
        )
        for repo in sorted(by_repo)
    ]
    selected_ids = {str(row["instance_id"]) for row in representatives}
    remainder = sorted(
        (row for row in eligible if str(row["instance_id"]) not in selected_ids),
        key=lambda row: (_rank(seed, str(row["instance_id"])), row["instance_id"]),
    )
    selected = representatives + remainder[: size - len(representatives)]
    selected_instance_ids = [str(row["instance_id"]) for row in selected]
    source_distribution = Counter(str(row["repo"]) for row in rows)
    eligible_distribution = Counter(str(row["repo"]) for row in eligible)
    selected_distribution = Counter(str(row["repo"]) for row in selected)

    return {
        "schema_version": 1,
        "selection_id": selection_id,
        "purpose": "coverage_oriented_swe_verified_quick_generalization_diagnostic",
        "dataset": source_manifest["dataset"],
        "dataset_revision": source_manifest["revision"],
        "source_manifest_sha256": file_sha256(manifest_path),
        "source_instance_count": len(rows),
        "eligible_instance_count": len(eligible),
        "selected_instance_ids": selected_instance_ids,
        "selected_cases": [
            {
                "instance_id": str(row["instance_id"]),
                "repo": str(row["repo"]),
                "selection_role": (
                    "repository_coverage"
                    if index < len(representatives)
                    else "global_hash_remainder"
                ),
                "rank_sha256": _rank(seed, str(row["instance_id"])),
            }
            for index, row in enumerate(selected)
        ],
        "excluded_instance_ids": sorted(excluded_instance_ids),
        "selection_policy": {
            "name": "one_per_repository_then_global_sha256_rank",
            "seed": seed,
            "target_size": size,
            "repository_coverage_count": len(representatives),
            "outcome_independent": True,
            "uses_plan_or_pce_pcce_outcomes": False,
            "membership_substitution_on_acquisition_failure": False,
        },
        "repository_distribution": {
            "source": dict(sorted(source_distribution.items())),
            "eligible": dict(sorted(eligible_distribution.items())),
            "selected": dict(sorted(selected_distribution.items())),
        },
        "usage_constraints": {
            "development_quick_validation": True,
            "untouched_holdout": False,
            "same_membership_required_for_pce_seed_pcce_c4_pcce": True,
            "smoke_cases_excluded": True,
            "selection_must_not_change_after_observing_outcomes": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--selection-id", required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--exclude-selection-manifest", type=Path)
    parser.add_argument("--exclude-instance", action="append", default=[])
    args = parser.parse_args()

    excluded = set(args.exclude_instance)
    if args.exclude_selection_manifest:
        exclusion = json.loads(
            args.exclude_selection_manifest.read_text(encoding="utf-8")
        )
        if exclusion.get("source_manifest_sha256") != file_sha256(
            args.source_snapshot / "manifest.json"
        ):
            raise SystemExit("exclusion manifest belongs to another source snapshot")
        excluded.update(str(value) for value in exclusion["selected_instance_ids"])

    payload = freeze_selection(
        source_snapshot=args.source_snapshot,
        selection_id=args.selection_id,
        seed=args.seed,
        size=args.size,
        excluded_instance_ids=excluded,
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
                "selected": len(payload["selected_instance_ids"]),
                "repositories": len(payload["repository_distribution"]["selected"]),
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
