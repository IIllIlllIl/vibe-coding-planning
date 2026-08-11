#!/usr/bin/env python3
"""Freeze the official PolyBench Python source rows before PCE generation."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_hash(row: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def freeze(
    source_csv: Path,
    output_dir: Path,
    *,
    revision: str,
    expected_instances: int,
    instance_ids: tuple[str, ...] = (),
) -> dict:
    csv.field_size_limit(100 * 1024 * 1024)
    with source_csv.open(newline="", encoding="utf-8") as handle:
        python_rows = [
            {
                str(key): "" if value is None else str(value)
                for key, value in row.items()
            }
            for row in csv.DictReader(handle)
            if str(row.get("language", "")).lower() == "python"
        ]
    if instance_ids:
        by_id = {row.get("instance_id", ""): row for row in python_rows}
        missing_ids = [
            instance_id for instance_id in instance_ids if instance_id not in by_id
        ]
        if missing_ids:
            raise ValueError(
                f"requested PolyBench instances are missing: {missing_ids}"
            )
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("requested PolyBench instance IDs must be unique")
        rows = [by_id[instance_id] for instance_id in instance_ids]
    else:
        rows = python_rows
    if len(rows) != expected_instances:
        raise ValueError(
            f"expected {expected_instances} Python instances, found {len(rows)}"
        )
    ids = [row.get("instance_id", "") for row in rows]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("PolyBench source instance IDs must be present and unique")
    required = {
        "instance_id",
        "problem_statement",
        "repo",
        "base_commit",
        "language",
        "test_patch",
        "test_command",
    }
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"PolyBench source CSV lacks required fields: {missing}")

    temporary = output_dir.with_name(output_dir.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    instances_path = temporary / "instances.jsonl"
    with instances_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "row_sha256": _row_hash(row),
                        "dockerfile_sha256": hashlib.sha256(
                            row.get("dockerfile", "").encode("utf-8")
                        ).hexdigest(),
                        "source_row": row,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    manifest = {
        "schema_version": 1,
        "snapshot_id": output_dir.name,
        "complete": True,
        "provisional": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "AmazonScience/SWE-PolyBench",
        "revision": revision,
        "language": "Python",
        "instances": len(rows),
        "selection": (
            {"kind": "explicit_instance_ids", "instance_ids": list(instance_ids)}
            if instance_ids
            else {"kind": "all_python"}
        ),
        "instance_ids_sha256": hashlib.sha256(
            "\n".join(ids).encode("utf-8")
        ).hexdigest(),
        "source_csv": str(source_csv.resolve()),
        "source_csv_sha256": _sha(source_csv),
        "instances_file": "instances.jsonl",
        "instances_sha256": _sha(instances_path),
    }
    (temporary / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if output_dir.exists():
        existing = json.loads(
            (output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        comparable = {
            key: value for key, value in manifest.items() if key != "created_at"
        }
        old = {key: value for key, value in existing.items() if key != "created_at"}
        shutil.rmtree(temporary)
        if comparable != old:
            raise ValueError(f"existing frozen snapshot differs: {output_dir}")
        return existing
    temporary.replace(output_dir)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--expected-instances", type=int, default=199)
    parser.add_argument(
        "--instance-id",
        action="append",
        default=[],
        help="Freeze only this instance; repeat to preserve an explicit smoke order",
    )
    args = parser.parse_args()
    manifest = freeze(
        args.source_csv,
        args.output_dir,
        revision=args.revision,
        expected_instances=args.expected_instances,
        instance_ids=tuple(args.instance_id),
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
