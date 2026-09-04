#!/usr/bin/env python3
"""Audit existing SWE-Verified SIF bytes into a retrospective manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.environment.apptainer_env import _image_to_sif_name  # noqa: E402
from src.swe_verified_pce.dataset import canonical_image_ref, file_sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--sif-cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--selection-manifest", type=Path)
    parser.add_argument("--verify-base-commits", action="store_true")
    parser.add_argument("--apptainer-bin", default="apptainer")
    args = parser.parse_args()

    source_manifest_path = args.source_snapshot / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    rows_path = args.source_snapshot / str(source_manifest["instances_file"])
    rows = [
        json.loads(line)["source_row"]
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    selected_ids = None
    selection_sha = None
    if args.selection_manifest is not None:
        selection = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        if selection.get("source_manifest_sha256") != file_sha256(source_manifest_path):
            raise SystemExit("selection manifest belongs to another source snapshot")
        selected_ids = set(str(value) for value in selection["selected_instance_ids"])
        source_ids = {str(row["instance_id"]) for row in rows}
        missing = sorted(selected_ids - source_ids)
        if missing:
            raise SystemExit("selection contains unknown IDs: " + ", ".join(missing))
        selection_sha = file_sha256(args.selection_manifest)
    records = {}
    for row in rows:
        instance_id = str(row["instance_id"])
        if selected_ids is not None and instance_id not in selected_ids:
            continue
        image_ref = canonical_image_ref(instance_id)
        sif = args.sif_cache_dir / _image_to_sif_name(image_ref)
        if sif.is_file():
            base_verified = False
            base_output = ""
            if args.verify_base_commits:
                command = [
                    args.apptainer_bin,
                    "exec",
                    str(sif),
                    "sh",
                    "-lc",
                    "cd /testbed && git cat-file -e "
                    + shlex.quote(str(row["base_commit"]) + "^{commit}"),
                ]
                result = subprocess.run(
                    command, capture_output=True, text=True, check=False
                )
                base_verified = result.returncode == 0
                base_output = (result.stdout + result.stderr)[-2000:]
            records[image_ref] = {
                "instance_id": instance_id,
                "status": "audited",
                "sif_path": str(sif),
                "sif_bytes": sif.stat().st_size,
                "sif_sha256": file_sha256(sif),
                "provenance_strength": "retrospective",
                "oci_digest": None,
                "expected_base_commit": str(row["base_commit"]),
                "base_commit_verified": base_verified,
                "base_commit_verification_output": base_output,
            }
        else:
            records[image_ref] = {
                "instance_id": instance_id,
                "status": "missing",
                "sif_path": str(sif),
                "provenance_strength": "unavailable",
                "expected_base_commit": str(row["base_commit"]),
                "base_commit_verified": False,
            }
    payload = {
        "schema_version": 1,
        "purpose": "swe_verified_sif_snapshot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "selection_manifest_sha256": selection_sha,
        "records": records,
    }
    payload["manifest_id"] = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)
    print(json.dumps({"records": len(records), "manifest_id": payload["manifest_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
