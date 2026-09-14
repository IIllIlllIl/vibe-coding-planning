#!/usr/bin/env python3
"""Freeze a small outcome-balanced ACE flow/prompt smoke selection."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _rank(seed: int, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()


def _balanced(rows: list[dict[str, Any]], count: int, seed: int) -> list[str]:
    if count < 2 or count % 2:
        raise ValueError("smoke split count must be a positive even number")
    target = count // 2
    selected: list[str] = []
    for resolved in (True, False):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["resolved"] is resolved:
                groups[row["repo"]].append(row)
        for group in groups.values():
            group.sort(key=lambda row: _rank(seed, row["instance_id"]))
        label_ids = []
        depth = 0
        while len(label_ids) < target:
            added = False
            for repo in sorted(groups):
                if depth < len(groups[repo]):
                    label_ids.append(groups[repo][depth]["instance_id"])
                    added = True
                    if len(label_ids) == target:
                        break
            if not added:
                raise ValueError("not enough cases for balanced smoke selection")
            depth += 1
        selected.extend(label_ids)
    return selected


def build(
    *, snapshot: Path, output: Path, train_count: int, validation_count: int, seed: int
) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to modify existing output: {output}")
    manifest_path = snapshot / "manifest.json"
    train_path = snapshot / "train.jsonl"
    validation_path = snapshot / "validation.jsonl"
    train = _read_jsonl(train_path)
    validation = _read_jsonl(validation_path)
    train_ids = _balanced(train, train_count, seed)
    validation_ids = _balanced(validation, validation_count, seed)
    payload = {
        "schema_version": 1,
        "purpose": "outcome_exposed_ace_flow_and_prompt_smoke",
        "not_method_evaluation": True,
        "source_snapshot": str(snapshot),
        "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "source_train_sha256": hashlib.sha256(train_path.read_bytes()).hexdigest(),
        "source_validation_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest(),
        "selection_policy": "label_balanced_repository_round_robin_hash_v1",
        "seed": seed,
        "train_instance_ids": train_ids,
        "validation_instance_ids": validation_ids,
        "train_count": len(train_ids),
        "validation_count": len(validation_ids),
        "train_resolved": train_count // 2,
        "train_unresolved": train_count // 2,
        "validation_resolved": validation_count // 2,
        "validation_unresolved": validation_count // 2,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train-count", type=int, default=32)
    parser.add_argument("--validation-count", type=int, default=8)
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
