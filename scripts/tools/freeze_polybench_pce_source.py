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


def _image_ref(instance_id: str) -> str:
    return (
        "ghcr.io/timesler/swe-polybench.eval.x86_64."
        f"{instance_id.lower()}:v1.1"
    )


def freeze(
    source_csv: Path,
    output_dir: Path,
    *,
    revision: str,
    expected_instances: int,
    instance_ids: tuple[str, ...] = (),
    image_provenance: Path | None = None,
    image_provenance_origin: str | None = None,
    unavailable_evidence: Path | None = None,
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
    image_selection: dict | None = None
    image_manifest: dict | None = None
    if image_provenance is not None:
        if instance_ids:
            raise ValueError(
                "image-provenance selection cannot be combined with instance IDs"
            )
        image_manifest = json.loads(image_provenance.read_text(encoding="utf-8"))
        if not image_manifest.get("complete"):
            raise ValueError("image provenance must be complete")
        records = image_manifest.get("records")
        if not isinstance(records, dict):
            raise ValueError("image provenance has no records mapping")
        missing = [
            row["instance_id"]
            for row in python_rows
            if _image_ref(row["instance_id"]) not in records
        ]
        if missing:
            raise ValueError(f"image provenance lacks source instances: {missing}")
        unexpected = sorted(
            {
                str(records[_image_ref(row["instance_id"])].get("status"))
                for row in python_rows
            }
            - {"cached", "pulled", "failed"}
        )
        if unexpected:
            raise ValueError(f"image provenance has unexpected statuses: {unexpected}")
        rows = [
            row
            for row in python_rows
            if records[_image_ref(row["instance_id"])].get("status")
            in {"cached", "pulled"}
        ]
        unavailable = len(python_rows) - len(rows)
        image_selection = {
            "kind": "exact_v1.1_available_images",
            "accepted_statuses": ["cached", "pulled"],
            "source_instances": len(python_rows),
            "available_instances": len(rows),
            "unavailable_instances": unavailable,
            "tag": "v1.1",
            "tag_fallback": False,
            "local_build_fallback": False,
        }
        if unavailable_evidence is not None:
            unavailable_manifest = json.loads(
                unavailable_evidence.read_text(encoding="utf-8")
            )
            unavailable_rows = unavailable_manifest.get("unavailable_images")
            if not isinstance(unavailable_rows, list):
                raise ValueError("unavailable evidence has no unavailable_images list")
            expected_unavailable = {
                row["instance_id"]
                for row in python_rows
                if records[_image_ref(row["instance_id"])].get("status") == "failed"
            }
            observed_unavailable = {
                str(item.get("instance_id"))
                for item in unavailable_rows
                if isinstance(item, dict)
            }
            if observed_unavailable != expected_unavailable:
                raise ValueError(
                    "unavailable evidence differs from failed image provenance"
                )
    elif instance_ids:
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
    image_fields = {}
    if image_provenance is not None and image_manifest is not None:
        frozen_images = temporary / "images.json"
        shutil.copyfile(image_provenance, frozen_images)
        image_fields = {
            "image_manifest_file": frozen_images.name,
            "image_manifest_sha256": _sha(frozen_images),
            "image_manifest_origin": image_provenance_origin,
            "image_selection": image_selection,
        }
        if unavailable_evidence is not None:
            frozen_unavailable = temporary / "unavailable-images.json"
            shutil.copyfile(unavailable_evidence, frozen_unavailable)
            image_fields.update(
                {
                    "unavailable_evidence_file": frozen_unavailable.name,
                    "unavailable_evidence_sha256": _sha(frozen_unavailable),
                }
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
        "selection": image_selection
        or (
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
        **image_fields,
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
    parser.add_argument(
        "--image-provenance",
        type=Path,
        help="Select only exact-v1.1 cached/pulled images and freeze this manifest",
    )
    parser.add_argument(
        "--image-provenance-origin",
        help="Authoritative operational path recorded for the copied manifest",
    )
    parser.add_argument(
        "--unavailable-evidence",
        type=Path,
        help="Freeze and cross-check the image-unavailability evidence",
    )
    args = parser.parse_args()
    manifest = freeze(
        args.source_csv,
        args.output_dir,
        revision=args.revision,
        expected_instances=args.expected_instances,
        instance_ids=tuple(args.instance_id),
        image_provenance=args.image_provenance,
        image_provenance_origin=args.image_provenance_origin,
        unavailable_evidence=args.unavailable_evidence,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
