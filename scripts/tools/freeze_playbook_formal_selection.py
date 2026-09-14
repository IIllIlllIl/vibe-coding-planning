#!/usr/bin/env python3
"""Freeze a deterministic repository/outcome-stratified ACE selection."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rank(seed: int, scope: str, value: str) -> str:
    return hashlib.sha256(f"{seed}\0{scope}\0{value}".encode()).hexdigest()


def _stratified_ids(
    rows: list[dict[str, Any]], *, count: int, seed: int, split: str
) -> list[str]:
    if count < 1 or count > len(rows):
        raise ValueError(f"invalid {split} selection count")
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["repo"]), bool(row["resolved"]))].append(row)

    quotas: dict[tuple[str, bool], int] = {}
    fractions: dict[tuple[str, bool], float] = {}
    for key, group in groups.items():
        exact = len(group) * count / len(rows)
        quotas[key] = math.floor(exact)
        fractions[key] = exact - quotas[key]
    remaining = count - sum(quotas.values())
    order = sorted(
        groups,
        key=lambda key: (
            -fractions[key],
            _rank(seed, f"{split}-stratum", f"{key[0]}:{key[1]}"),
        ),
    )
    for key in order:
        if remaining == 0:
            break
        if quotas[key] < len(groups[key]):
            quotas[key] += 1
            remaining -= 1
    if remaining:
        raise AssertionError(f"could not allocate complete {split} selection")

    selected = set()
    for key, group in groups.items():
        ordered = sorted(
            group,
            key=lambda row: _rank(seed, split, str(row["instance_id"])),
        )
        selected.update(
            str(row["instance_id"]) for row in ordered[: quotas[key]]
        )
    # Preserve the immutable snapshot order after deterministic membership choice.
    return [str(row["instance_id"]) for row in rows if row["instance_id"] in selected]


def build(
    *, snapshot: Path, output: Path, train_count: int, validation_count: int, seed: int
) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to modify existing output: {output}")
    manifest_path = snapshot / "manifest.json"
    train_path = snapshot / "train.jsonl"
    validation_path = snapshot / "validation.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, path in (("train.jsonl", train_path), ("validation.jsonl", validation_path)):
        if _sha256(path) != manifest["artifacts"][name]:
            raise ValueError(f"source snapshot artifact drift: {name}")
    train = _read_jsonl(train_path)
    validation = _read_jsonl(validation_path)
    if len(train) != manifest["train_instances"]:
        raise ValueError("train count does not match the source manifest")
    if len(validation) != manifest["validation_instances"]:
        raise ValueError("validation count does not match the source manifest")

    train_ids = _stratified_ids(train, count=train_count, seed=seed, split="train")
    validation_ids = _stratified_ids(
        validation, count=validation_count, seed=seed, split="validation"
    )
    selected = set(train_ids) | set(validation_ids)
    all_rows = [*train, *validation]
    selected_rows = [row for row in all_rows if row["instance_id"] in selected]
    excluded_rows = [row for row in all_rows if row["instance_id"] not in selected]
    if len(selected_rows) != train_count + validation_count:
        raise AssertionError("formal selection size mismatch")

    payload = {
        "schema_version": 1,
        "purpose": "safe_pce_reliable_ace_formal_development_selection",
        "not_held_out_evaluation": True,
        "source_snapshot": str(snapshot),
        "source_manifest_sha256": _sha256(manifest_path),
        "source_train_sha256": _sha256(train_path),
        "source_validation_sha256": _sha256(validation_path),
        "selection_policy": "within_split_repo_outcome_hamilton_hash_v1",
        "outcome_used_for_sampling": True,
        "seed": seed,
        "train_instance_ids": train_ids,
        "validation_instance_ids": validation_ids,
        "train_count": len(train_ids),
        "validation_count": len(validation_ids),
        "selected_count": len(selected_rows),
        "selected_resolved": sum(bool(row["resolved"]) for row in selected_rows),
        "selected_unresolved": sum(not bool(row["resolved"]) for row in selected_rows),
        "train_resolved": sum(
            bool(row["resolved"]) for row in train if row["instance_id"] in selected
        ),
        "train_unresolved": sum(
            not bool(row["resolved"]) for row in train if row["instance_id"] in selected
        ),
        "validation_resolved": sum(
            bool(row["resolved"])
            for row in validation
            if row["instance_id"] in selected
        ),
        "validation_unresolved": sum(
            not bool(row["resolved"])
            for row in validation
            if row["instance_id"] in selected
        ),
        "repository_distribution": dict(sorted(Counter(
            str(row["repo"]) for row in selected_rows
        ).items())),
        "excluded_from_formal_selection": [
            {
                "instance_id": row["instance_id"],
                "split": row["split"],
                "repo": row["repo"],
                "resolved": row["resolved"],
            }
            for row in excluded_rows
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-count", type=int, default=320)
    parser.add_argument("--validation-count", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    build(
        snapshot=args.snapshot,
        output=args.output,
        train_count=args.train_count,
        validation_count=args.validation_count,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
