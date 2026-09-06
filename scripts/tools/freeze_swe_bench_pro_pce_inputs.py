#!/usr/bin/env python3
"""Freeze selected Pro rows and pinned official evaluator assets for PCE."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pyarrow.parquet as pq


DATASET = "ScaleAI/SWE-bench_Pro"
REQUIRED = {
    "repo", "instance_id", "base_commit", "patch", "test_patch",
    "problem_statement", "requirements", "interface", "fail_to_pass",
    "pass_to_pass", "before_repo_set_cmd", "selected_test_files_to_run",
    "dockerhub_tag",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _task(row: dict[str, Any]) -> str:
    parts = [str(row["problem_statement"]).strip()]
    requirements = str(row.get("requirements") or "").strip()
    interface = str(row.get("interface") or "").strip()
    if requirements:
        parts.append("Requirements:\n" + requirements)
    if interface:
        parts.append("New interfaces introduced:\n" + interface)
    return "\n\n".join(parts)


def freeze(
    *, parquet: Path, source_manifest_path: Path, selection_manifest_path: Path,
    request_manifest_path: Path, official_checkout: Path,
    official_revision: str, output_dir: Path,
) -> dict[str, Any]:
    source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_manifest_path.read_text(encoding="utf-8"))
    requests = json.loads(request_manifest_path.read_text(encoding="utf-8"))
    if file_sha256(parquet) != source["source_parquet_sha256"]:
        raise ValueError("Parquet differs from frozen Pro source")
    if selection.get("source_manifest_sha256") != file_sha256(source_manifest_path):
        raise ValueError("selection belongs to another source manifest")
    if requests.get("selection_id") != selection.get("selection_id"):
        raise ValueError("request and selection identities differ")
    head = subprocess.run(
        ["git", "-C", str(official_checkout), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if head != official_revision:
        raise ValueError("official evaluator checkout revision differs")
    table = pq.read_table(parquet)
    missing = sorted(REQUIRED - set(table.column_names))
    if missing:
        raise ValueError("Pro Parquet lacks fields: " + ", ".join(missing))
    by_id = {str(row["instance_id"]): row for row in table.to_pylist()}
    selected_ids = [str(item["instance_id"]) for item in selection["selected_cases"]]
    request_by_id = {str(item["instance_id"]): item for item in requests["requests"]}
    wrappers = []
    for instance_id in selected_ids:
        row = dict(by_id[instance_id])
        request = request_by_id[instance_id]
        for field in ("repo", "base_commit", "dockerhub_tag"):
            if str(row[field]) != str(request[field]):
                raise ValueError(f"{instance_id}: request differs for {field}")
        source_assets = official_checkout / "run_scripts" / instance_id
        source_files = {
            "run_script": source_assets / "run_script.sh",
            "parser": source_assets / "parser.py",
            "base_dockerfile": official_checkout / "dockerfiles" / "base_dockerfile" / instance_id / "Dockerfile",
            "instance_dockerfile": official_checkout / "dockerfiles" / "instance_dockerfile" / instance_id / "Dockerfile",
        }
        target = output_dir / "official_evaluator_assets" / instance_id
        target.mkdir(parents=True, exist_ok=True)
        assets: dict[str, str] = {}
        for name, source_file in source_files.items():
            if not source_file.is_file():
                raise ValueError(f"{instance_id}: official asset missing: {source_file}")
            suffix = ".sh" if name == "run_script" else ".py" if name == "parser" else ".Dockerfile"
            destination = target / f"{name}{suffix}"
            shutil.copy2(source_file, destination)
            assets[name] = str(destination.relative_to(output_dir))
            assets[f"{name}_sha256"] = file_sha256(destination)
        row_hash = hashlib.sha256(_stable(row).encode()).hexdigest()
        wrappers.append({
            "instance_id": instance_id,
            "row_sha256": row_hash,
            "issue_description": _task(row),
            "evaluator_assets": assets,
            "source_row": row,
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    instances_path = output_dir / "instances.jsonl"
    instances_path.write_text("".join(_stable(row) + "\n" for row in wrappers), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_quick_pce_source_snapshot",
        "dataset": DATASET,
        "revision": source["revision"],
        "complete": True,
        "provisional": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_parquet_sha256": file_sha256(parquet),
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "selection_manifest_sha256": file_sha256(selection_manifest_path),
        "request_manifest_sha256": file_sha256(request_manifest_path),
        "official_evaluator_revision": official_revision,
        "official_evaluator_assets": ["run_script.sh", "parser.py", "base Dockerfile", "instance Dockerfile"],
        "official_task_rendering": "problem_statement + Requirements + New interfaces introduced",
        "instances": len(wrappers),
        "instance_ids": selected_ids,
        "instances_file": instances_path.name,
        "instances_sha256": file_sha256(instances_path),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--selection-manifest", required=True, type=Path)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--official-checkout", required=True, type=Path)
    parser.add_argument("--official-revision", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = freeze(
        parquet=args.parquet,
        source_manifest_path=args.source_manifest,
        selection_manifest_path=args.selection_manifest,
        request_manifest_path=args.request_manifest,
        official_checkout=args.official_checkout,
        official_revision=args.official_revision,
        output_dir=args.output_dir,
    )
    print(json.dumps({"instances": result["instances"], "revision": result["revision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
