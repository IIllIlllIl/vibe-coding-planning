#!/usr/bin/env python3
"""Freeze one exact SWE-bench Verified Parquet revision for PCE/PCCE."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


DATASET = "SWE-bench/SWE-bench_Verified"
REQUIRED_FIELDS = {
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "difficulty",
    "version",
}


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-parquet-sha256")
    args = parser.parse_args()

    parquet = args.parquet.resolve()
    parquet_hash = _file_hash(parquet)
    if args.expected_parquet_sha256 and parquet_hash != args.expected_parquet_sha256:
        raise SystemExit("Parquet SHA-256 differs from --expected-parquet-sha256")
    table = pq.read_table(parquet)
    missing = sorted(REQUIRED_FIELDS - set(table.column_names))
    if missing:
        raise SystemExit("SWE-Verified Parquet lacks fields: " + ", ".join(missing))
    rows = table.to_pylist()
    ids = [str(row["instance_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit("SWE-Verified instance IDs are not unique")

    wrappers = []
    for row in rows:
        normalized = dict(row)
        serialized = _stable(normalized).encode("utf-8")
        wrappers.append(
            {
                "instance_id": str(normalized["instance_id"]),
                "row_sha256": _hash_bytes(serialized),
                "source_row": normalized,
            }
        )
    lines = "".join(_stable(wrapper) + "\n" for wrapper in wrappers)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    instances_path = args.output_dir / "instances.jsonl"
    manifest_path = args.output_dir / "manifest.json"
    temporary = instances_path.with_suffix(".jsonl.tmp")
    temporary.write_text(lines, encoding="utf-8")
    temporary.replace(instances_path)
    manifest = {
        "schema_version": 1,
        "purpose": "swe_verified_pce_source_snapshot",
        "dataset": DATASET,
        "revision": args.revision,
        "complete": True,
        "provisional": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_parquet_sha256": parquet_hash,
        "source_parquet_bytes": parquet.stat().st_size,
        "instances": len(wrappers),
        "instance_ids_sha256": _hash_bytes("\n".join(ids).encode("utf-8")),
        "instances_file": instances_path.name,
        "instances_sha256": _file_hash(instances_path),
        "required_fields": sorted(REQUIRED_FIELDS),
    }
    temp_manifest = manifest_path.with_suffix(".json.tmp")
    temp_manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_manifest.replace(manifest_path)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
