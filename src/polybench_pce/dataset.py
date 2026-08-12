"""Load frozen PolyBench rows and exact image provenance."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from src.polybench_pce.models import FrozenImage, PolyBenchPCECase


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_image_ref(instance_id: str) -> str:
    return f"ghcr.io/timesler/swe-polybench.eval.x86_64.{instance_id.lower()}:v1.1"


def _list(value: Any, *, field: str, instance_id: str) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"{instance_id}: {field} is not a parseable list") from exc
        if isinstance(parsed, list):
            return tuple(str(item) for item in parsed)
    raise ValueError(f"{instance_id}: {field} is not a list")


def load_polybench_pce_cases(
    snapshot: Path,
    image_manifest_path: Path,
) -> tuple[list[PolyBenchPCECase], dict[str, Any], dict[str, Any]]:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("PCE requires a complete, non-provisional source snapshot")
    if manifest.get("dataset") != "AmazonScience/SWE-PolyBench":
        raise ValueError("unexpected PCE source dataset")
    if manifest.get("language") != "Python":
        raise ValueError("PCE source snapshot must contain Python only")
    rows_path = snapshot / str(manifest.get("instances_file", "instances.jsonl"))
    if file_sha256(rows_path) != manifest.get("instances_sha256"):
        raise ValueError("PCE source rows differ from their frozen hash")
    rows = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != int(manifest.get("instances", -1)):
        raise ValueError("PCE source instance count mismatch")

    images = json.loads(image_manifest_path.read_text(encoding="utf-8"))
    records = images.get("records")
    if not isinstance(records, dict):
        raise ValueError("image provenance manifest has no records mapping")
    cases: list[PolyBenchPCECase] = []
    for wrapper in rows:
        row = wrapper.get("source_row")
        if not isinstance(row, dict):
            raise ValueError("PCE instance lacks source_row")
        instance_id = str(row["instance_id"])
        expected_row_hash = hashlib.sha256(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if wrapper.get("row_sha256") != expected_row_hash:
            raise ValueError(f"{instance_id}: source row hash mismatch")
        image_ref = canonical_image_ref(instance_id)
        image_raw = records.get(image_ref)
        if not isinstance(image_raw, dict):
            raise ValueError(f"{instance_id}: exact v1.1 image record is missing")
        if image_raw.get("status") not in {"cached", "pulled"}:
            # Image availability is an environment outcome, not an unresolved
            # task label. A complete provenance manifest may therefore retain
            # failed image rows while PCE runs only the image-available subset.
            continue
        if not image_raw.get("sif_sha256") or not image_raw.get("sif_path"):
            raise ValueError(f"{instance_id}: image record lacks frozen SIF identity")
        if image_raw.get("provenance_strength") not in {
            "pull_attested",
            "retrospective",
        }:
            raise ValueError(f"{instance_id}: image provenance is not reviewable")
        if not image_raw.get("oci_digest"):
            raise ValueError(f"{instance_id}: image record lacks OCI digest")
        cases.append(
            PolyBenchPCECase(
                instance_id=instance_id,
                row_sha256=expected_row_hash,
                issue_description=str(row["problem_statement"]),
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                language=str(row["language"]),
                task_category=str(row.get("task_category", "")),
                test_patch=str(row["test_patch"]),
                f2p=_list(
                    row.get("F2P", row.get("f2p", [])),
                    field="F2P",
                    instance_id=instance_id,
                ),
                p2p=_list(
                    row.get("P2P", row.get("p2p", [])),
                    field="P2P",
                    instance_id=instance_id,
                ),
                test_command=str(row["test_command"]),
                image=FrozenImage(
                    requested_ref=image_ref,
                    sif_path=str(image_raw["sif_path"]),
                    sif_sha256=str(image_raw["sif_sha256"]),
                    sif_bytes=int(image_raw["sif_bytes"]),
                    provenance_strength=str(image_raw.get("provenance_strength", "")),
                    oci_digest=(
                        str(image_raw["oci_digest"])
                        if image_raw.get("oci_digest")
                        else None
                    ),
                ),
                source_row=dict(row),
            )
        )
    if len({case.instance_id for case in cases}) != len(cases):
        raise ValueError("PCE source instance IDs are not unique")
    return cases, manifest, images
