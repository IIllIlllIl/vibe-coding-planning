#!/usr/bin/env python3
"""Freeze the clean69 and balanced40 PolyBench ACE-PCCE selections."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any


TMP_PATH_RE = re.compile(r"/tmp(?:/[A-Za-z0-9_.{}$-]+)+")
PROTOCOL_TMP_PATH = "/tmp/plan.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _tmp_paths(value: Any) -> set[str]:
    paths = set(TMP_PATH_RE.findall(json.dumps(value, ensure_ascii=False)))
    return {
        path.rstrip(".,;:)`\"'")
        for path in paths
        if path.rstrip(".,;:)`\"'") != PROTOCOL_TMP_PATH
    }


def _has_abrupt_ending(plan: str) -> bool:
    tail = plan.rstrip()
    while True:
        stripped = re.sub(r"(?:\*\*|__|`)+$", "", tail).rstrip()
        if stripped == tail:
            return tail.endswith(":")
        tail = stripped


def _largest_remainder_quotas(counts: Counter[str], target: int) -> dict[str, int]:
    total = sum(counts.values())
    if target > total:
        raise ValueError("selection target exceeds available cases")
    exact = {repo: target * count / total for repo, count in counts.items()}
    quotas = {repo: int(value) for repo, value in exact.items()}
    remaining = target - sum(quotas.values())
    order = sorted(counts, key=lambda repo: (-(exact[repo] - quotas[repo]), repo))
    for repo in order[:remaining]:
        quotas[repo] += 1
    return dict(sorted((repo, count) for repo, count in quotas.items() if count))


def _rank(seed: str, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()


def build(
    source: Path,
    dependency_manifest: Path,
    output: Path,
    *,
    selection_seed: str,
) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to modify existing output: {output}")

    source_manifest_path = source / "manifest.json"
    source_validation_path = source / "validation.jsonl"
    source_outcomes_path = source / "paired_pce_outcomes.jsonl"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    rows = _read_jsonl(source_validation_path)
    outcomes = _read_jsonl(source_outcomes_path)
    if len(rows) != 99 or source_manifest.get("cleaned", {}).get("instances") != 99:
        raise ValueError("expected the frozen 99-case clean PolyBench authority")
    outcome_by_id = {row["instance_id"]: row for row in outcomes}
    if set(outcome_by_id) != {row["instance_id"] for row in rows}:
        raise ValueError("validation and paired PCE outcome memberships differ")

    dependency = json.loads(dependency_manifest.read_text(encoding="utf-8"))
    dependency_ids = set(dependency["instances"]) & set(outcome_by_id)

    audit: list[dict[str, Any]] = []
    for row in rows:
        instance_id = row["instance_id"]
        source_output_path = Path(outcome_by_id[instance_id]["source_output_path"])
        source_output = json.loads(source_output_path.read_text(encoding="utf-8"))
        planner_paths = _tmp_paths(source_output.get("plan_trajectory", []))
        coder_paths = _tmp_paths(source_output.get("code_trajectory", []))
        shared_paths = sorted(planner_paths & coder_paths)
        reasons: list[str] = []
        if shared_paths:
            reasons.append("SHARED_PLANNER_CODER_TMP_ARTIFACT")
        if instance_id in dependency_ids:
            reasons.append("EVALUATOR_DEPENDENCY_CACHE_SCOPE")
        if _has_abrupt_ending(row["checker_input"]["plan"]):
            reasons.append("ABRUPT_ENDING_PLAN")
        audit.append(
            {
                "action": "exclude" if reasons else "retain",
                "instance_id": instance_id,
                "pce_resolved": row["resolved"],
                "reason_codes": reasons,
                "repository": row["checker_input"]["repository"]["repo"],
                "shared_non_protocol_tmp_paths": shared_paths,
            }
        )

    excluded_ids = {
        item["instance_id"] for item in audit if item["action"] == "exclude"
    }
    retained = [row for row in rows if row["instance_id"] not in excluded_ids]
    if len(retained) != 69:
        raise ValueError(f"expected 69 retained cases, got {len(retained)}")

    by_label_repo: dict[bool, dict[str, list[dict[str, Any]]]] = {
        True: defaultdict(list),
        False: defaultdict(list),
    }
    for row in retained:
        by_label_repo[bool(row["resolved"])][
            row["checker_input"]["repository"]["repo"]
        ].append(row)

    selected: list[dict[str, Any]] = []
    quotas_by_label: dict[str, dict[str, int]] = {}
    for resolved, label in ((True, "resolved"), (False, "unresolved")):
        counts = Counter(
            {repo: len(repo_rows) for repo, repo_rows in by_label_repo[resolved].items()}
        )
        quotas = _largest_remainder_quotas(counts, 20)
        quotas_by_label[label] = quotas
        for repo, quota in quotas.items():
            repo_rows = sorted(
                by_label_repo[resolved][repo],
                key=lambda row: (
                    _rank(selection_seed, row["instance_id"]),
                    row["instance_id"],
                ),
            )
            selected.extend(repo_rows[:quota])
    selected_ids = {row["instance_id"] for row in selected}
    selected_ordered = [
        row["instance_id"] for row in retained if row["instance_id"] in selected_ids
    ]
    if len(selected_ordered) != 40:
        raise AssertionError("balanced selection must contain exactly 40 cases")

    output.mkdir(parents=True)
    audit_path = output / "cleaning_audit.jsonl"
    clean_path = output / "clean69.json"
    selection_path = output / "balanced40.json"
    _write_jsonl(audit_path, audit)

    reason_counts = Counter(
        reason
        for item in audit
        if item["action"] == "exclude"
        for reason in item["reason_codes"]
    )
    exclusion_overlap = Counter(
        "+".join(item["reason_codes"])
        for item in audit
        if item["action"] == "exclude"
    )
    clean_manifest = {
        "schema_version": 1,
        "selection_id": "polybench-ace-pcce-clean69-v1-20260911",
        "purpose": (
            "Eligibility-cleaned PolyBench development universe for ACE PCCE; "
            "not an untouched holdout or confirmatory generalization set"
        ),
        "source_snapshot_id": source_manifest["snapshot_id"],
        "source_manifest_sha256": _sha256(source_manifest_path),
        "source_validation_sha256": _sha256(source_validation_path),
        "source_pce_outcomes_sha256": _sha256(source_outcomes_path),
        "dependency_manifest_sha256": _sha256(dependency_manifest),
        "cleaning_policy": {
            "tmp": (
                "exclude only cases where frozen historical Planner and Code "
                "trajectories reference the same non-protocol /tmp path"
            ),
            "dependency": (
                "exclude final-clean99 members of the frozen evaluator dependency-"
                "cache/network scope"
            ),
            "truncation": (
                "exclude Plans ending at an introductory colon after trailing "
                "Markdown cleanup"
            ),
        },
        "source_cases": len(rows),
        "excluded_cases": len(excluded_ids),
        "retained_cases": len(retained),
        "retained_pce_resolved": sum(bool(row["resolved"]) for row in retained),
        "retained_pce_unresolved": sum(not bool(row["resolved"]) for row in retained),
        "reason_counts": dict(sorted(reason_counts.items())),
        "exclusion_combinations": dict(sorted(exclusion_overlap.items())),
        "cleaning_audit_file": audit_path.name,
        "cleaning_audit_sha256": _sha256(audit_path),
        "selected_instance_ids": [row["instance_id"] for row in retained],
        "interpretation_boundary": (
            "The cleaning uses historical execution topology and evaluator-"
            "dependency evidence. It does not use candidate behavior or later ACE "
            "PCCE outcomes, but PolyBench has already informed method development."
        ),
    }
    _write_json(clean_path, clean_manifest)

    selected_by_repo = Counter(
        row["checker_input"]["repository"]["repo"] for row in selected
    )
    selection_manifest = {
        "schema_version": 1,
        "selection_id": "polybench-ace-pcce-balanced40-v1-20260911",
        "purpose": (
            "Balanced development evaluation of ACE PCCE intervention behavior; "
            "not a prevalence sample, untouched holdout, or confirmatory result"
        ),
        "parent_selection": clean_path.name,
        "parent_selection_sha256": _sha256(clean_path),
        "cleaning_audit_sha256": _sha256(audit_path),
        "selection_policy": {
            "target": "20 PCE-resolved and 20 PCE-unresolved cases",
            "repository_allocation": (
                "largest-remainder proportional allocation within each PCE label"
            ),
            "within_stratum_order": (
                "ascending SHA256(seed + NUL + instance_id), then instance_id"
            ),
            "seed": selection_seed,
            "repository_quotas": quotas_by_label,
        },
        "selected_instance_ids": selected_ordered,
        "baseline_composition": {
            "pce_resolved": 20,
            "pce_unresolved": 20,
            "total": 40,
            "repositories": dict(sorted(selected_by_repo.items())),
        },
        "interpretation_boundary": (
            "PCE failures are deliberately enriched to measure interception, "
            "preservation, and intervention-mediated transitions. Aggregate rates "
            "are not estimates of clean69 prevalence."
        ),
    }
    _write_json(selection_path, selection_manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dependency-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--selection-seed",
        default="polybench-ace-pcce-balanced40-v1-20260911",
    )
    args = parser.parse_args()
    build(
        args.source,
        args.dependency_manifest,
        args.output,
        selection_seed=args.selection_seed,
    )


if __name__ == "__main__":
    main()
