#!/usr/bin/env python3
"""Combine Pro acquisition and official-SIF audit evidence for PCE."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def finalize(
    *,
    source_snapshot: Path,
    original_provenance: Path,
    recovery_overlay: Path,
    direct_audit: Path,
    output: Path,
) -> dict[str, Any]:
    source_manifest = source_snapshot / "manifest.json"
    source = _load(source_manifest)
    wrappers = [
        json.loads(line)
        for line in (source_snapshot / source["instances_file"])
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    rows = {wrapper["source_row"]["instance_id"]: wrapper["source_row"] for wrapper in wrappers}
    if len(rows) != 25:
        raise ValueError("expected the frozen Pro quick25 source")

    original = _load(original_provenance)
    overlay = _load(recovery_overlay)
    acquisition = {
        image: record
        for image, record in original["records"].items()
        if record.get("status") in {"cached", "pulled"}
    }
    for run in overlay["runs"]:
        for record in run.get("records", []):
            image = record["requested_ref"]
            if image in acquisition:
                raise ValueError(f"duplicate Pro acquisition identity: {image}")
            acquisition[image] = record
    if len(acquisition) != 25:
        raise ValueError("Pro acquisition evidence does not cover quick25")

    audit_value = _load(direct_audit)
    audits = {record["instance_id"]: record for record in audit_value["records"]}
    if audit_value.get("summary", {}).get("audited") != 25 or len(audits) != 25:
        raise ValueError("direct Pro SIF audit is incomplete")
    records = {}
    for instance_id, row in rows.items():
        image = "jefzda/sweap-images:" + row["dockerhub_tag"]
        acquired = acquisition.get(image)
        audit = audits.get(instance_id)
        if not acquired or not audit:
            raise ValueError(f"{instance_id}: missing acquisition or audit evidence")
        if (
            audit.get("status") != "audited"
            or audit.get("base_commit") != row["base_commit"]
            or audit.get("head") != row["base_commit"]
            or audit.get("base_commit_available") is not True
            or audit.get("gold_test_commit_available") is not True
            or not isinstance(audit.get("non_ancestor_count"), int)
            or audit["non_ancestor_count"] <= 0
            or int(acquired["sif_bytes"]) != int(audit["sif_bytes"])
        ):
            raise ValueError(f"{instance_id}: Pro SIF audit does not establish exposure")
        records[image] = {
            "instance_id": instance_id,
            "status": "audited",
            "sif_path": acquired["sif_path"],
            "sif_bytes": int(acquired["sif_bytes"]),
            "sif_sha256": acquired["sif_sha256"],
            "provenance_strength": acquired.get(
                "provenance_strength", "recovery_sha256_attested"
            ),
            "oci_digest": acquired.get("oci_digest"),
            "expected_base_commit": row["base_commit"],
            "base_commit_verified": True,
            "official_sif_non_ancestor_commit_count": audit["non_ancestor_count"],
            "official_sif_ref_count": audit["ref_count"],
            "gold_test_commit_available_in_official_sif": True,
        }
    value = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_pce_sif_snapshot",
        "source_manifest_sha256": file_sha256(source_manifest),
        "selection_manifest_sha256": source["selection_manifest_sha256"],
        "original_provenance_sha256": file_sha256(original_provenance),
        "recovery_overlay_sha256": file_sha256(recovery_overlay),
        "direct_history_audit_sha256": file_sha256(direct_audit),
        "agent_workspace_policy": "official_sif_workspace_v1",
        "history_contamination_policy": "observed_git_history_access_v1",
        "latent_future_history_present": True,
        "official_build_artifacts_preserved": True,
        "network_policy": "unchanged_from_current_swe_pce",
        "records": records,
    }
    value["manifest_id"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output.exists() and output.read_text(encoding="utf-8") != rendered:
        raise ValueError(f"refusing to overwrite different Pro image manifest: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--original-provenance", required=True, type=Path)
    parser.add_argument("--recovery-overlay", required=True, type=Path)
    parser.add_argument("--direct-audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    value = finalize(**vars(parser.parse_args()))
    print(json.dumps({"records": len(value["records"]), "manifest_id": value["manifest_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
