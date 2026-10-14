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
import tempfile


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--source-snapshot", type=Path)
    inputs.add_argument("--request-manifest", type=Path)
    parser.add_argument("--repository", help="Restrict acquisition requests to one repository")
    parser.add_argument("--sif-cache-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apptainer-bin", default="apptainer")
    args = parser.parse_args()
    if args.request_manifest:
        return audit_requests(args)
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


def audit_requests(args) -> int:
    """Audit acquisition metadata only, without importing solution/test fields."""
    request = json.loads(args.request_manifest.read_text())
    rows = [r for r in request["requests"] if not args.repository or r["repo"] == args.repository]
    if not rows:
        raise ValueError("Empty audit selection")
    records = {}
    for row in rows:
        image = row["image_ref"]
        sif = args.sif_cache_dir / row["sif_filename"]
        verified = False
        location = None
        output = ""
        if sif.is_file():
            declared_location = row.get("working_directory")
            if declared_location:
                if not isinstance(declared_location, str) or not declared_location.startswith("/"):
                    raise ValueError("working_directory must be an absolute container path")
                # A frozen location is authoritative; do not accept another repository.
                locations = [declared_location]
            else:
                # Retain the Pro discovery path for inputs without a frozen location.
                probe = subprocess.run(
                    [args.apptainer_bin, "exec", str(sif), "sh", "-lc",
                     "find /app /testbed /workspace /workspaces /root /opt -maxdepth 3 -name .git -type d 2>/dev/null || true"],
                    capture_output=True, text=True, timeout=120, check=False,
                )
                locations = sorted(set(line[:-5] for line in probe.stdout.splitlines() if line.endswith('/.git')))
            matches = []
            for candidate in locations:
                with tempfile.TemporaryDirectory(prefix="vibe-sif-audit-home-") as home:
                    (Path(home) / '.gitconfig').write_text('[safe]\n\tdirectory = ' + json.dumps(candidate) + '\n')
                    check = subprocess.run(
                        [args.apptainer_bin, "exec", "--bind", home + ":/tmp/vibe-sif-audit-home:ro", str(sif),
                         "env", "HOME=/tmp/vibe-sif-audit-home", "GIT_CONFIG_GLOBAL=/tmp/vibe-sif-audit-home/.gitconfig",
                         "git", "-C", candidate, "cat-file", "-e", str(row["base_commit"]) + "^{commit}"],
                        capture_output=True, text=True, timeout=120, check=False,
                    )
                if check.returncode == 0:
                    matches.append(candidate)
                else:
                    output = (check.stdout + check.stderr)[-2000:]
            verified = len(matches) == 1
            location = matches[0] if verified else None
            if not verified:
                output = f"Expected exactly one repository containing base commit; matches={matches}; candidates={locations}; {output}"
        records[image] = {
            "instance_id": row["instance_id"], "status": "audited" if sif.is_file() and verified else "invalid",
            "sif_path": str(sif), "sif_bytes": sif.stat().st_size if sif.is_file() else None,
            "sif_sha256": file_sha256(sif) if sif.is_file() else None,
            "expected_base_commit": row["base_commit"], "base_commit_verified": verified,
            "repository_path": location, "base_commit_verification_output": output,
            "provenance_strength": "retrospective", "oci_digest": None,
        }
    payload = {"schema_version":1, "purpose":"swe_bench_pro_v2_sif_snapshot", "created_at":datetime.now(timezone.utc).isoformat(),
               "source_request_manifest_sha256":file_sha256(args.request_manifest), "records":records,
               "agent_history_policy":None, "agent_history_audit":"not performed; separate prepared history required before Agent execution"}
    payload["manifest_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\n')
    valid = sum(r["base_commit_verified"] for r in records.values())
    print(json.dumps({"records":len(rows), "base_verified":valid}), flush=True)
    return 0 if valid == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
