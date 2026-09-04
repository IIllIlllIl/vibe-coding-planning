#!/usr/bin/env python3
"""Freeze the immutable inputs consumed by the SWE-chat login preheater."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from huggingface_hub import HfApi


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def content_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def build_source_manifest(info: Any, dataset_id: str) -> dict[str, Any]:
    files = []
    for sibling in sorted(info.siblings, key=lambda item: item.rfilename):
        lfs = getattr(sibling, "lfs", None)
        files.append(
            {
                "path": sibling.rfilename,
                "bytes": sibling.size,
                "blob_id": getattr(sibling, "blob_id", None),
                "lfs_sha256": None if lfs is None else lfs.sha256,
                "lfs_bytes": None if lfs is None else lfs.size,
            }
        )
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_frozen_source_manifest",
        "dataset_id": dataset_id,
        "revision": info.sha,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"] or 0) for item in files),
        "files": files,
    }
    manifest["content_sha256"] = content_sha256(manifest)
    return manifest


def build_repository_request_manifest(
    repositories_path: Path,
    *,
    dataset_id: str,
    revision: str,
) -> dict[str, Any]:
    rows = pq.read_table(repositories_path, columns=["repo_id", "url"]).to_pylist()
    requests = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for index, row in enumerate(rows):
        repo_id = str(row["repo_id"] or "")
        url = str(row["url"] or "").rstrip("/")
        if not repo_id or repo_id.count("/") != 1:
            raise ValueError(f"repository row {index} has invalid repo_id {repo_id!r}")
        if url != f"https://github.com/{repo_id}":
            raise ValueError(f"repository row {index} has unexpected URL {url!r}")
        if repo_id in seen_ids or url in seen_urls:
            raise ValueError(f"repository row {index} is duplicated: {repo_id}")
        seen_ids.add(repo_id)
        seen_urls.add(url)
        requests.append({"index": index, "repo_id": repo_id, "url": url})
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_repository_request_manifest",
        "dataset_id": dataset_id,
        "revision": revision,
        "repositories_parquet_sha256": file_sha256(repositories_path),
        "requested_count": len(requests),
        "requests": requests,
    }
    manifest["content_sha256"] = content_sha256(manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--dataset-id", default="SALT-NLP/SWE-chat")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repositories-parquet", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    info = HfApi().dataset_info(
        args.dataset_id,
        revision=args.revision,
        files_metadata=True,
    )
    if info.sha != args.revision:
        raise SystemExit(f"resolved revision {info.sha} != requested {args.revision}")
    source = build_source_manifest(info, args.dataset_id)
    repositories = build_repository_request_manifest(
        args.repositories_parquet,
        dataset_id=args.dataset_id,
        revision=args.revision,
    )
    atomic_json(args.output_dir / "dataset-source-manifest.json", source)
    atomic_json(args.output_dir / "repository-request-manifest.json", repositories)
    print(
        json.dumps(
            {
                "event": "swe_chat_preheat_inputs_frozen",
                "source_manifest_sha256": source["content_sha256"],
                "repository_manifest_sha256": repositories["content_sha256"],
                "files": source["file_count"],
                "repositories": repositories["requested_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
