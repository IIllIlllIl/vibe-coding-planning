"""Build a deterministic balanced 6/4 GEPA pilot subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = (
    REPO_ROOT
    / "output/SWE-bench_Verified/verified-round1-gepa-datasets"
    / "20260614_482_fdc056ae85df"
)
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "output/SWE-bench_Verified/gepa-pilot-datasets"
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _select_balanced(
    records: list[dict[str, Any]],
    *,
    per_label: int,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    selected = []
    for resolved in (False, True):
        candidates = [
            record for record in records if record["resolved"] is resolved
        ]
        candidates.sort(key=lambda record: record["instance_id"])
        rng.shuffle(candidates)
        repo_counts: Counter[str] = Counter()
        remaining = list(candidates)
        while remaining and sum(
            record["resolved"] is resolved for record in selected
        ) < per_label:
            remaining.sort(
                key=lambda record: (
                    repo_counts[record["repo"]],
                    record["instance_id"],
                )
            )
            chosen = remaining.pop(0)
            selected.append(chosen)
            repo_counts[chosen["repo"]] += 1
    if len(selected) != per_label * 2:
        raise ValueError("source does not contain enough balanced cases")
    return sorted(selected, key=lambda record: record["instance_id"])


def build_pilot_dataset(
    source: Path,
    output_root: Path,
    *,
    train_per_label: int = 3,
    validation_per_label: int = 2,
    seed: int = 42,
) -> Path:
    train = _select_balanced(
        _read_jsonl(source / "train.jsonl"),
        per_label=train_per_label,
        seed=seed,
    )
    validation = _select_balanced(
        _read_jsonl(source / "validation.jsonl"),
        per_label=validation_per_label,
        seed=seed + 1,
    )
    identity = json.dumps(
        {
            "source": source.name,
            "seed": seed,
            "train": [record["instance_id"] for record in train],
            "validation": [record["instance_id"] for record in validation],
        },
        sort_keys=True,
    ).encode()
    snapshot_id = (
        f"pilot_{len(train)}_{len(validation)}_seed{seed}_"
        f"{hashlib.sha256(identity).hexdigest()[:12]}"
    )
    output = output_root / snapshot_id
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "train.jsonl", train)
    _write_jsonl(output / "validation.jsonl", validation)
    manifest = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "complete": True,
        "provisional": False,
        "pilot": True,
        "source_snapshot": str(source),
        "selection_policy": "balanced-label-repo-diverse-v1",
        "seed": seed,
        "train_instances": len(train),
        "validation_instances": len(validation),
        "train_resolved": sum(record["resolved"] for record in train),
        "validation_resolved": sum(
            record["resolved"] for record in validation
        ),
        "train_instance_ids": [record["instance_id"] for record in train],
        "validation_instance_ids": [
            record["instance_id"] for record in validation
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-per-label", type=int, default=3)
    parser.add_argument("--validation-per-label", type=int, default=2)
    args = parser.parse_args()
    print(
        build_pilot_dataset(
            args.source,
            args.output_root,
            train_per_label=args.train_per_label,
            validation_per_label=args.validation_per_label,
            seed=args.seed,
        )
    )


if __name__ == "__main__":
    main()
