#!/usr/bin/env python3
"""Freeze SWE-bench Pro source identity and Python image requests.

The source manifest is semantic experiment identity.  The image request
manifest is a deterministic projection for acquisition; it intentionally
contains no patches, tests, issue text, or other solution-bearing fields.
Registry availability and downloaded SIF bytes are operational evidence and
must be recorded separately.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


DATASET = "ScaleAI/SWE-bench_Pro"
REQUIRED_FIELDS = {
    "repo",
    "instance_id",
    "base_commit",
    "repo_language",
    "dockerhub_tag",
}


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sif_name(image: str) -> str:
    safe = image.replace("/", "_").replace(":", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    return f"{safe}.sif"


def _read_id_list(path: Path) -> list[str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Expected a JSON string list: {path}")
    if len(value) != len(set(value)):
        raise ValueError(f"Duplicate instance IDs: {path}")
    return value


def _write_frozen(path: Path, value: Any) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"Refusing to overwrite different frozen content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def freeze(
    *,
    parquet: Path,
    revision: str,
    output_dir: Path,
    expected_parquet_sha256: str | None = None,
    legacy_python_ids: Path | None = None,
    legacy_ansible_ids: Path | None = None,
) -> dict[str, Any]:
    parquet = parquet.resolve()
    parquet_sha256 = _file_hash(parquet)
    if expected_parquet_sha256 and parquet_sha256 != expected_parquet_sha256:
        raise ValueError("Parquet SHA-256 differs from the expected authoritative hash")

    table = pq.read_table(parquet)
    missing = sorted(REQUIRED_FIELDS - set(table.column_names))
    if missing:
        raise ValueError("SWE-bench Pro Parquet lacks fields: " + ", ".join(missing))
    rows = table.to_pylist()
    all_ids = [str(row["instance_id"]) for row in rows]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("SWE-bench Pro instance IDs are not unique")

    python_rows = [
        row for row in rows if str(row["repo_language"]).strip().casefold() == "python"
    ]
    python_rows.sort(key=lambda row: str(row["instance_id"]))
    requests: list[dict[str, Any]] = []
    for ordinal, row in enumerate(python_rows):
        tag = str(row["dockerhub_tag"]).strip()
        base_commit = str(row["base_commit"]).strip()
        if not tag or not base_commit:
            raise ValueError(f"Missing image tag or base commit: {row['instance_id']}")
        image_ref = f"jefzda/sweap-images:{tag}"
        requests.append(
            {
                "ordinal": ordinal,
                "instance_id": str(row["instance_id"]),
                "repo": str(row["repo"]),
                "repo_language": str(row["repo_language"]),
                "base_commit": base_commit,
                "dockerhub_tag": tag,
                "image_ref": image_ref,
                "sif_filename": _sif_name(image_ref),
            }
        )

    python_ids = [request["instance_id"] for request in requests]
    if len(python_ids) != len(set(python_ids)):
        raise ValueError("Python request instance IDs are not unique")
    if len({request["image_ref"] for request in requests}) != len(requests):
        raise ValueError("Python image references are not unique")

    legacy: dict[str, Any] = {}
    if legacy_python_ids:
        ids = _read_id_list(legacy_python_ids)
        legacy["python266"] = {
            "file_sha256": _file_hash(legacy_python_ids),
            "count": len(ids),
            "exact_set_match": set(ids) == set(python_ids),
            "exact_order_match": ids == python_ids,
        }
        if not legacy["python266"]["exact_set_match"]:
            raise ValueError("Legacy Python IDs do not match the frozen Python universe")
    if legacy_ansible_ids:
        ids = _read_id_list(legacy_ansible_ids)
        ansible_ids = {
            request["instance_id"]
            for request in requests
            if request["repo"].casefold() == "ansible/ansible"
        }
        legacy["mac_ansible96"] = {
            "file_sha256": _file_hash(legacy_ansible_ids),
            "count": len(ids),
            "exact_set_match": set(ids) == ansible_ids,
            "subset_of_python_universe": set(ids).issubset(python_ids),
        }
        if not legacy["mac_ansible96"]["exact_set_match"]:
            raise ValueError("Legacy Ansible IDs do not match the frozen Ansible subset")

    ids_sha256 = _hash_bytes(("\n".join(python_ids) + "\n").encode())
    requests_sha256 = _hash_bytes((_stable(requests) + "\n").encode())
    source_manifest = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_source_identity",
        "dataset": DATASET,
        "revision": revision,
        "source_file": "data/test-00000-of-00001.parquet",
        "source_parquet_sha256": parquet_sha256,
        "source_parquet_bytes": parquet.stat().st_size,
        "rows": len(rows),
        "instance_ids_sha256": _hash_bytes(("\n".join(all_ids) + "\n").encode()),
        "columns": list(table.column_names),
        "python_selection": {
            "predicate": "casefold(trim(repo_language)) == 'python'",
            "count": len(requests),
            "instance_ids_sha256": ids_sha256,
            "repositories": dict(sorted(Counter(r["repo"] for r in requests).items())),
        },
        "legacy_consistency": legacy,
    }
    request_manifest = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_python_sif_acquisition_requests",
        "dataset": DATASET,
        "revision": revision,
        "source_parquet_sha256": parquet_sha256,
        "selection": "repo_language == Python",
        "request_count": len(requests),
        "instance_ids_sha256": ids_sha256,
        "requests_sha256": requests_sha256,
        "contains_solution_or_test_evidence": False,
        "requests": requests,
    }
    _write_frozen(output_dir / "dataset-source-manifest.json", source_manifest)
    _write_frozen(output_dir / "python-image-request-manifest.json", request_manifest)
    _write_frozen(output_dir / "python-instance-ids.json", python_ids)
    return {"source": source_manifest, "requests": request_manifest}


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-parquet-sha256")
    parser.add_argument("--legacy-python-ids", type=Path)
    parser.add_argument("--legacy-ansible-ids", type=Path)
    args = parser.parse_args()
    result = freeze(
        parquet=args.parquet,
        revision=args.revision,
        output_dir=args.output_dir,
        expected_parquet_sha256=args.expected_parquet_sha256,
        legacy_python_ids=args.legacy_python_ids,
        legacy_ansible_ids=args.legacy_ansible_ids,
    )
    print(json.dumps({"rows": result["source"]["rows"], "requests": result["requests"]["request_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
