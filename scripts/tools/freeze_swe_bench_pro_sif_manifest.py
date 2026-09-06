#!/usr/bin/env python3
"""Audit selected Pro SIF bytes and official base commits for PCE."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--sif-cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apptainer-bin", default="apptainer")
    args = parser.parse_args()
    manifest_path = args.source_snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line)["source_row"] for line in (args.source_snapshot / manifest["instances_file"]).read_text().splitlines() if line]
    records = {}
    for row in rows:
        image = f"jefzda/sweap-images:{row['dockerhub_tag']}"
        safe = "".join(character for character in image.replace("/", "_").replace(":", "_") if character.isalnum() or character in "._-") + ".sif"
        sif = args.sif_cache_dir / safe
        verified = False
        output = ""
        non_ancestor_count = None
        gold_test_commit = None
        gold_test_commit_available = False
        if sif.is_file():
            result = subprocess.run(
                [args.apptainer_bin, "exec", str(sif), "sh", "-lc", "cd /app && git -c safe.directory=/app cat-file -e " + shlex.quote(str(row["base_commit"]) + "^{commit}")],
                capture_output=True, text=True, check=False,
            )
            verified = result.returncode == 0
            output = (result.stdout + result.stderr)[-2000:]
            history = subprocess.run(
                [
                    args.apptainer_bin,
                    "exec",
                    str(sif),
                    "sh",
                    "-lc",
                    "cd /app && git -c safe.directory=/app rev-list --all --not "
                    + shlex.quote(str(row["base_commit"]))
                    + " | wc -l",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if history.returncode == 0 and history.stdout.strip().isdigit():
                non_ancestor_count = int(history.stdout.strip())
            match = re.search(
                r"^git checkout ([0-9a-f]{40}) -- ",
                str(row["before_repo_set_cmd"]),
                flags=re.MULTILINE,
            )
            if match:
                gold_test_commit = match.group(1)
                target = subprocess.run(
                    [
                        args.apptainer_bin,
                        "exec",
                        str(sif),
                        "sh",
                        "-lc",
                        "cd /app && git -c safe.directory=/app cat-file -e "
                        + shlex.quote(gold_test_commit + "^{commit}"),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                gold_test_commit_available = target.returncode == 0
        records[image] = {
            "instance_id": row["instance_id"],
            "status": "audited" if sif.is_file() else "missing",
            "sif_path": str(sif),
            "sif_bytes": sif.stat().st_size if sif.is_file() else None,
            "sif_sha256": file_sha256(sif) if sif.is_file() else None,
            "provenance_strength": "retrospective" if sif.is_file() else "unavailable",
            "oci_digest": None,
            "expected_base_commit": row["base_commit"],
            "base_commit_verified": verified,
            "base_commit_verification_output": output,
            "agent_visible_non_ancestor_commit_count": non_ancestor_count,
            "gold_test_commit": gold_test_commit,
            "gold_test_commit_available": gold_test_commit_available,
        }
    payload = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_pce_sif_snapshot",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": file_sha256(manifest_path),
        "selection_manifest_sha256": manifest["selection_manifest_sha256"],
        "agent_history_policy": None,
        "agent_history_audit": (
            "non-ancestor commits are measured but not contained; this manifest "
            "does not authorize Plan/Code execution"
        ),
        "records": records,
    }
    payload["manifest_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "base_verified": sum(r["base_commit_verified"] for r in records.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
