#!/usr/bin/env python3
"""Freeze an already acquired official RPG snapshot and Python SIF requests."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.tools.freeze_swe_bench_pro_metadata import _file_hash, _sif_name, _write_frozen

DATASET = "Xin-Zhou-smu/SWE-RPG-Bench"


def freeze(*, source_root: Path, git_tree: Path, revision: str, output_dir: Path,
           expected_tasks: int = 163, expected_python: int = 78) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A full Git commit is required")
    tree = json.loads(git_tree.read_text())
    if (tree.get("truncated") or tree.get("source_commit") != revision
            or tree["sha"] != tree.get("commit_tree_sha")):
        raise ValueError("Git tree is truncated or differs from the frozen revision")
    files = []
    for item in tree["tree"]:
        if item["type"] != "blob":
            continue
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe source path")
        path = source_root / relative
        raw = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != item["sha"] or len(raw) != item["size"]:
            raise ValueError(f"Source differs from pinned Git blob: {relative}")
        files.append({"path":str(relative), "bytes":len(raw), "sha256":hashlib.sha256(raw).hexdigest(), "git_blob_sha1":blob})
    rows = [json.loads(line) for line in (source_root / "data/benchmark.jsonl").read_text().splitlines() if line.strip()]
    ids = [r["instance_id"] for r in rows]
    if len(rows) != expected_tasks or len(set(ids)) != len(ids):
        raise ValueError("Unexpected task count or duplicate IDs")
    image_source = json.loads((source_root / "data/images.json").read_text())
    image_rows = image_source["images"]
    image_map = {r["instance_id"]:r for r in image_rows}
    if len(image_rows) != len(image_map) or set(image_map) != set(ids):
        raise ValueError("Image mapping does not match task universe")
    requests = []
    for row in sorted((r for r in rows if r["pl"] == "python"), key=lambda r:r["instance_id"]):
        ref = row["docker_image"]
        if not re.fullmatch(r"[a-z0-9._/-]+@sha256:[0-9a-f]{64}", ref):
            raise ValueError("Image is not SHA-256 pinned")
        if image_map[row["instance_id"]]["image"] != ref or image_map[row["instance_id"]]["digest"] != ref.split("@")[1]:
            raise ValueError("Dataset and image mapping disagree")
        if not re.fullmatch(r"[0-9a-f]{40}", row["base_commit"]):
            raise ValueError("Missing or malformed base commit")
        if row["working_dir"] not in {"/testbed", "/testbed2"}:
            raise ValueError("Unexpected repository location")
        requests.append({"ordinal":len(requests), "instance_id":row["instance_id"], "repo":row["repo"],
                         "base_commit":row["base_commit"], "repo_language":"Python", "working_directory":row["working_dir"],
                         "image_ref":ref, "oci_digest":ref.split("@")[1], "sif_filename":_sif_name(ref)})
    if len(requests) != expected_python or len({r["sif_filename"] for r in requests}) != len(requests):
        raise ValueError("Unexpected Python count or SIF filename collision")
    base = {"schema_version":1, "dataset":DATASET, "revision":revision}
    source = {**base, "purpose":"swe_rpg_source_identity", "source_root":str(source_root),
              "files":files, "tasks":len(rows), "languages":dict(Counter(r["pl"] for r in rows)),
              "raw_authority_contains_solution_and_test_evidence":True,
              "source_benchmark_sha256":_file_hash(source_root / "data/benchmark.jsonl")}
    request = {**base, "purpose":"swe_rpg_python_sif_acquisition_requests", "selection":"pl == python", "request_count":len(requests),
               "source_benchmark_sha256":source["source_benchmark_sha256"], "contains_solution_or_test_evidence":False, "requests":requests}
    selection = {**base, "purpose":"swe_rpg_python_selection", "predicate":"pl == python", "count":len(requests),
                 "instance_ids":[r["instance_id"] for r in requests], "repositories":dict(sorted(Counter(r["repo"] for r in requests).items()))}
    for name,value in [("dataset-source-manifest.json",source),("python-image-request-manifest.json",request),
                       ("selection-manifest.json",selection),("python-instance-ids.json",selection["instance_ids"]),
                       ("preheat-images.json",{"images":[r["image_ref"] for r in requests]})]:
        _write_frozen(output_dir / name, value)
    return {"source":source, "requests":request}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root",type=Path,required=True)
    parser.add_argument("--git-tree",type=Path,required=True)
    parser.add_argument("--revision",required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args = parser.parse_args()
    result = freeze(**vars(args))
    print(json.dumps({"tasks":result['source']['tasks'], "python_requests":len(result['requests']['requests']), "source_files":len(result['source']['files'])}))
