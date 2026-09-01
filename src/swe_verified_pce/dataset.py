"""Load fixed-revision SWE-Verified rows and frozen SIF identities."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from src.evaluator.swe_evaluator import derive_image_name
from src.swe_verified_pce.models import FrozenImage, SWEVerifiedPCECase


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_image_ref(instance_id: str) -> str:
    return derive_image_name({"instance_id": instance_id})


def _string_list(value: Any, *, field: str, instance_id: str) -> tuple[str, ...]:
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


def load_swe_verified_pce_cases(
    snapshot: Path,
    image_manifest_path: Path,
) -> tuple[list[SWEVerifiedPCECase], dict[str, Any], dict[str, Any]]:
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("SWE-Verified PCE requires a complete source snapshot")
    if manifest.get("dataset") != "SWE-bench/SWE-bench_Verified":
        raise ValueError("unexpected SWE-Verified source dataset")
    if not manifest.get("revision"):
        raise ValueError("SWE-Verified source snapshot lacks a fixed revision")
    rows_path = snapshot / str(manifest.get("instances_file", "instances.jsonl"))
    if file_sha256(rows_path) != manifest.get("instances_sha256"):
        raise ValueError("SWE-Verified source rows differ from their frozen hash")
    rows = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != int(manifest.get("instances", -1)):
        raise ValueError("SWE-Verified source instance count mismatch")

    images = json.loads(image_manifest_path.read_text(encoding="utf-8"))
    if images.get("source_manifest_sha256") != file_sha256(manifest_path):
        raise ValueError(
            "SWE-Verified image manifest belongs to another source snapshot"
        )
    records = images.get("records")
    if not isinstance(records, dict):
        raise ValueError("SWE-Verified image manifest has no records mapping")
    cases: list[SWEVerifiedPCECase] = []
    for wrapper in rows:
        row = wrapper.get("source_row")
        if not isinstance(row, dict):
            raise ValueError("SWE-Verified instance lacks source_row")
        instance_id = str(row["instance_id"])
        row_hash = hashlib.sha256(
            json.dumps(
                row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if wrapper.get("row_sha256") != row_hash:
            raise ValueError(f"{instance_id}: source row hash mismatch")
        image_ref = canonical_image_ref(instance_id)
        image_raw = records.get(image_ref)
        if not isinstance(image_raw, dict):
            if images.get("selection_manifest_sha256") is not None:
                continue
            raise ValueError(f"{instance_id}: frozen image record is missing")
        if image_raw.get("status") not in {"cached", "pulled", "audited"}:
            continue
        if image_raw.get("instance_id") != instance_id:
            raise ValueError(f"{instance_id}: frozen image record identity differs")
        if not image_raw.get("sif_sha256") or not image_raw.get("sif_path"):
            raise ValueError(f"{instance_id}: image record lacks frozen SIF identity")
        if image_raw.get("provenance_strength") not in {
            "pull_attested",
            "retrospective",
        }:
            raise ValueError(f"{instance_id}: image provenance is not reviewable")
        if image_raw.get("base_commit_verified") is not True:
            raise ValueError(f"{instance_id}: image lacks a verified base commit")
        cases.append(
            SWEVerifiedPCECase(
                instance_id=instance_id,
                row_sha256=row_hash,
                issue_description=str(row["problem_statement"]),
                repo=str(row["repo"]),
                base_commit=str(row["base_commit"]),
                version=str(row.get("version", "")),
                difficulty=str(row.get("difficulty", "")),
                environment_setup_commit=str(row.get("environment_setup_commit", "")),
                test_patch=str(row.get("test_patch", "")),
                fail_to_pass=_string_list(
                    row.get("FAIL_TO_PASS", []),
                    field="FAIL_TO_PASS",
                    instance_id=instance_id,
                ),
                pass_to_pass=_string_list(
                    row.get("PASS_TO_PASS", []),
                    field="PASS_TO_PASS",
                    instance_id=instance_id,
                ),
                gold_patch=str(row.get("patch", "")),
                image=FrozenImage(
                    requested_ref=image_ref,
                    sif_path=str(image_raw["sif_path"]),
                    sif_sha256=str(image_raw["sif_sha256"]),
                    sif_bytes=int(image_raw["sif_bytes"]),
                    provenance_strength=str(image_raw["provenance_strength"]),
                    oci_digest=str(image_raw["oci_digest"])
                    if image_raw.get("oci_digest")
                    else None,
                ),
                source_row=dict(row),
            )
        )
    if len({case.instance_id for case in cases}) != len(cases):
        raise ValueError("SWE-Verified source instance IDs are not unique")
    return cases, manifest, images
