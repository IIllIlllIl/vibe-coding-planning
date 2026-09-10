#!/usr/bin/env python3
"""Build an immutable eligibility-cleaned derivative of Verified Round 1."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any


TMP_PATH_RE = re.compile(r"/tmp(?:/[A-Za-z0-9_.{}$-]+)+")
KNOWN_WORKSPACE_CONFOUNDS = {
    "django__django-16136",
    "pylint-dev__pylint-4970",
}
TRIVIAL_PLAN_INSTANCES = {
    "django__django-10973",
    "django__django-12039",
    "django__django-13401",
    "django__django-13512",
    "sympy__sympy-22080",
}
INCOMPLETE_PLAN_INSTANCES = {
    "django__django-10097",
    "django__django-14351",
    "sympy__sympy-24539",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _tmp_paths(value: Any) -> set[str]:
    paths = set(TMP_PATH_RE.findall(json.dumps(value, ensure_ascii=False)))
    return {item.rstrip(".,;:)`\"'") for item in paths if item != "/tmp/plan.md"}


def _normalized_plan(row: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", row["checker_input"]["plan"].strip().casefold())


def _plan_quality_reasons(instance_id: str) -> list[str]:
    """Return frozen, human-audited Plan-artifact exclusions."""
    reasons = []
    if instance_id in TRIVIAL_PLAN_INSTANCES:
        reasons.append("TRIVIAL_PLACEHOLDER_PLAN")
    if instance_id in INCOMPLETE_PLAN_INSTANCES:
        reasons.append("TRUNCATED_OR_STRUCTURALLY_INCOMPLETE_PLAN")
    return reasons


def build(source: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to modify existing output: {output}")
    source_manifest_path = source / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    rows = _read_jsonl(source / "cases.jsonl")
    if len(rows) != 482 or source_manifest.get("selected_instances") != 482:
        raise ValueError("expected the frozen 482-case source")

    duplicate_groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        duplicate_groups[_normalized_plan(row)].append(row["instance_id"])
    duplicate_exclusions = {
        instance_id
        for ids in duplicate_groups.values() if len(ids) > 1
        for instance_id in sorted(ids)[1:]
    }

    ledger = []
    for row in rows:
        instance_id = row["instance_id"]
        evaluator = row["asi"]["evaluator_result"]
        plan_mentions_tmp = "/tmp" in row["checker_input"]["plan"].casefold()
        planner_paths = _tmp_paths(row["asi"]["plan_trajectory"])
        coder_paths = _tmp_paths(row["asi"]["code_trajectory"])
        shared_paths = sorted(planner_paths & coder_paths)
        reasons = []
        if (
            evaluator.get("error_info") is not None
            or evaluator.get("resolved") is not row["resolved"]
            or not isinstance(evaluator.get("report"), dict)
            or len(evaluator.get("report", {})) != 1
        ):
            reasons.append("OUTCOME_AUTHORITY_INVALID")
        if instance_id in KNOWN_WORKSPACE_CONFOUNDS:
            reasons.append("KNOWN_WORKSPACE_CONFOUND")
        if instance_id in duplicate_exclusions:
            reasons.append("EXACT_PLAN_DUPLICATE_EXTRA_COPY")
        if plan_mentions_tmp or shared_paths:
            reasons.append("TMP_TOPOLOGY_CONFOUND")
        reasons.extend(_plan_quality_reasons(instance_id))
        ledger.append({
            "instance_id": instance_id,
            "source_split": row["split"],
            "resolved": row["resolved"],
            "action": "exclude" if reasons else "retain",
            "reason_codes": reasons,
            "tmp_audit": {
                "final_plan_mentions_tmp": plan_mentions_tmp,
                "shared_non_plan_tmp_paths": shared_paths,
            },
        })

    excluded_ids = {item["instance_id"] for item in ledger if item["action"] == "exclude"}
    retained = [row for row in rows if row["instance_id"] not in excluded_ids]
    train = [row for row in retained if row["split"] == "train"]
    validation = [row for row in retained if row["split"] == "validation"]
    if len(retained) + len(excluded_ids) != len(rows):
        raise AssertionError("cleaning membership is not exhaustive")

    output.mkdir(parents=True)
    _write_jsonl(output / "cases.jsonl", retained)
    _write_jsonl(output / "train.jsonl", train)
    _write_jsonl(output / "validation.jsonl", validation)
    _write_jsonl(output / "audit_ledger.jsonl", ledger)
    exclusions = [item for item in ledger if item["action"] == "exclude"]
    _write_json(output / "exclusions.json", exclusions)
    reason_counts = Counter(reason for item in exclusions for reason in item["reason_codes"])
    manifest = {
        "schema_version": 1,
        "complete": True,
        "provisional": False,
        "immutable": True,
        "cleaning_policy": "verified-playbook-eligibility-v2",
        "source_snapshot": str(source),
        "source_manifest_sha256": _sha256(source_manifest_path),
        "source_cases_sha256": _sha256(source / "cases.jsonl"),
        "source_instances": len(rows),
        "retained_instances": len(retained),
        "excluded_instances": len(exclusions),
        "resolved": sum(row["resolved"] for row in retained),
        "unresolved": sum(not row["resolved"] for row in retained),
        "train_instances": len(train),
        "validation_instances": len(validation),
        "train_resolved": sum(row["resolved"] for row in train),
        "validation_resolved": sum(row["resolved"] for row in validation),
        "preserves_source_split": True,
        "reason_counts": dict(sorted(reason_counts.items())),
        "known_workspace_confounds": sorted(KNOWN_WORKSPACE_CONFOUNDS),
        "tmp_policy": "exclude final-plan /tmp mention or Planner/Coder shared non-plan /tmp path",
        "duplicate_policy": "retain lexicographically first exact normalized Plan",
        "plan_artifact_policy": (
            "exclude the frozen human-audited trivial placeholder and "
            "truncated or structurally incomplete Plan instances"
        ),
        "ordered_retained_ids_sha256": hashlib.sha256("\n".join(row["instance_id"] for row in retained).encode()).hexdigest(),
        "cases_sha256": _sha256(output / "cases.jsonl"),
        "train_sha256": _sha256(output / "train.jsonl"),
        "validation_sha256": _sha256(output / "validation.jsonl"),
        "audit_ledger_sha256": _sha256(output / "audit_ledger.jsonl"),
        "exclusions_sha256": _sha256(output / "exclusions.json"),
    }
    _write_json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()
