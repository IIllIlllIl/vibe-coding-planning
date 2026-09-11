#!/usr/bin/env python3
"""Freeze nested balanced20 and smoke10 ACE-PCCE development selections."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rank(seed: str, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()


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


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _select(
    universe: list[dict[str, Any]], *, per_label: int, seed: str
) -> tuple[list[str], dict[str, dict[str, int]]]:
    grouped: dict[bool, dict[str, list[dict[str, Any]]]] = {
        True: defaultdict(list),
        False: defaultdict(list),
    }
    for row in universe:
        grouped[bool(row["pce_resolved"])][row["repository"]].append(row)
    selected: set[str] = set()
    quotas: dict[str, dict[str, int]] = {}
    for label, name in ((True, "resolved"), (False, "unresolved")):
        counts = Counter(
            {repo: len(rows) for repo, rows in grouped[label].items()}
        )
        label_quotas = _largest_remainder_quotas(counts, per_label)
        quotas[name] = label_quotas
        for repo, quota in label_quotas.items():
            rows = sorted(
                grouped[label][repo],
                key=lambda row: (
                    _rank(seed, row["instance_id"]),
                    row["instance_id"],
                ),
            )
            selected.update(row["instance_id"] for row in rows[:quota])
    ordered = [row["instance_id"] for row in universe if row["instance_id"] in selected]
    if len(ordered) != 2 * per_label:
        raise AssertionError("balanced selection size mismatch")
    return ordered, quotas


def _manifest(
    *,
    selection_id: str,
    purpose: str,
    parent: Path,
    audit_sha256: str,
    selected: list[str],
    rows: dict[str, dict[str, Any]],
    quotas: dict[str, dict[str, int]],
    per_label: int,
    seed: str,
) -> dict[str, Any]:
    selected_rows = [rows[instance_id] for instance_id in selected]
    return {
        "schema_version": 1,
        "selection_id": selection_id,
        "purpose": purpose,
        "parent_selection": parent.name,
        "parent_selection_sha256": _sha256(parent),
        "cleaning_audit_sha256": audit_sha256,
        "selection_policy": {
            "target": f"{per_label} PCE-resolved and {per_label} PCE-unresolved cases",
            "repository_allocation": (
                "largest-remainder proportional allocation within each PCE label"
            ),
            "within_stratum_order": (
                "ascending SHA256(seed + NUL + instance_id), then instance_id"
            ),
            "seed": seed,
            "repository_quotas": quotas,
        },
        "selected_instance_ids": selected,
        "baseline_composition": {
            "pce_resolved": per_label,
            "pce_unresolved": per_label,
            "total": 2 * per_label,
            "repositories": dict(
                sorted(Counter(row["repository"] for row in selected_rows).items())
            ),
        },
        "interpretation_boundary": (
            "Failure-enriched ACE-PCCE development selection; not a prevalence "
            "sample, untouched holdout, or confirmatory result."
        ),
    }


def build(parent_dir: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError(f"refusing to modify existing output: {output}")
    parent40 = parent_dir / "balanced40.json"
    audit_path = parent_dir / "cleaning_audit.jsonl"
    parent = json.loads(parent40.read_text(encoding="utf-8"))
    audit = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_id = {row["instance_id"]: row for row in audit}
    universe = [by_id[item] for item in parent["selected_instance_ids"]]
    if len(universe) != 40:
        raise ValueError("expected frozen balanced40 parent")

    formal_seed = "polybench-ace-pcce-balanced20-v1-20260911"
    formal_ids, formal_quotas = _select(
        universe, per_label=10, seed=formal_seed
    )
    output.mkdir(parents=True)
    formal_path = output / "balanced20.json"
    _write(
        formal_path,
        _manifest(
            selection_id=formal_seed,
            purpose="Bounded Candidate-3 ACE-PCCE development evaluation",
            parent=parent40,
            audit_sha256=_sha256(audit_path),
            selected=formal_ids,
            rows=by_id,
            quotas=formal_quotas,
            per_label=10,
            seed=formal_seed,
        ),
    )

    smoke_seed = "polybench-ace-pcce-smoke10-v1-20260911"
    formal_rows = [by_id[item] for item in formal_ids]
    smoke_ids, smoke_quotas = _select(
        formal_rows, per_label=5, seed=smoke_seed
    )
    _write(
        output / "smoke10.json",
        _manifest(
            selection_id=smoke_seed,
            purpose="Ten-case ACE-PCCE end-to-end smoke",
            parent=formal_path,
            audit_sha256=_sha256(audit_path),
            selected=smoke_ids,
            rows=by_id,
            quotas=smoke_quotas,
            per_label=5,
            seed=smoke_seed,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.parent_dir, args.output)


if __name__ == "__main__":
    main()
