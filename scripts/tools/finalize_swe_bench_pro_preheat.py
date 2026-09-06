#!/usr/bin/env python3
"""Freeze a non-destructive availability overlay for Pro quick preheat runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


HASH_RE = re.compile(r"sif_sha256=([0-9a-f]{64})(?: instance_id=(\S+))?")
BYTES_RE = re.compile(r"sif_bytes=(\d+)(?: instance_id=(\S+))?")
FINAL_RE = re.compile(r"final_sif=(\S+)")
SUMMARY_RE = re.compile(
    r"summary cached=(\d+) pulled=(\d+) failed=(\d+) requested=(\d+)"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_frozen(path: Path, value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"refusing to overwrite different frozen content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def _single_smoke_record(log: str, request_by_sif: dict[str, dict[str, Any]]) -> dict[str, Any]:
    hashes = HASH_RE.findall(log)
    sizes = BYTES_RE.findall(log)
    finals = FINAL_RE.findall(log)
    if len(hashes) != 1 or len(sizes) != 1 or len(finals) != 1:
        raise ValueError("single-image smoke log lacks one complete SIF record")
    sif_name = Path(finals[0]).name
    request = request_by_sif.get(sif_name)
    if request is None:
        raise ValueError("single-image smoke SIF is not in the frozen request manifest")
    return {
        "instance_id": request["instance_id"],
        "requested_ref": request["image_ref"],
        "sif_path": finals[0],
        "sif_sha256": hashes[0][0],
        "sif_bytes": int(sizes[0][0]),
        "acquisition": "node_local_tmp_single_image_smoke",
    }


def _recovery_records(log: str, request_by_id: dict[str, dict[str, Any]], cache_dir: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    hashes = {instance: value for value, instance in HASH_RE.findall(log) if instance}
    sizes = {instance: int(value) for value, instance in BYTES_RE.findall(log) if instance}
    if set(hashes) != set(sizes):
        raise ValueError("recovery log hash/size identities differ")
    records = []
    for instance_id in sorted(hashes):
        request = request_by_id.get(instance_id)
        if request is None:
            raise ValueError(f"recovered instance is not frozen: {instance_id}")
        records.append(
            {
                "instance_id": instance_id,
                "requested_ref": request["image_ref"],
                "sif_path": str(Path(cache_dir) / request["sif_filename"]),
                "sif_sha256": hashes[instance_id],
                "sif_bytes": sizes[instance_id],
                "acquisition": "node_local_tmp_all_missing_recovery",
            }
        )
    summaries = SUMMARY_RE.findall(log)
    if len(summaries) != 1:
        raise ValueError("recovery log lacks exactly one terminal summary")
    cached, pulled, failed, requested = map(int, summaries[0])
    if pulled != len(records) or failed != 0 or cached + pulled != requested:
        raise ValueError("recovery terminal summary is not a complete success")
    return records, {
        "cached": cached,
        "pulled": pulled,
        "failed": failed,
        "requested": requested,
    }


def finalize(
    *,
    request_manifest: Path,
    original_provenance: Path,
    smoke_log: Path,
    recovery_log: Path,
    output: Path,
    smoke_job_id: str,
    recovery_job_id: str,
    cache_dir: str,
) -> dict[str, Any]:
    requests_value = json.loads(request_manifest.read_text(encoding="utf-8"))
    requests = requests_value.get("requests")
    if not isinstance(requests, list) or len(requests) != 25:
        raise ValueError("expected the frozen 25-request Pro manifest")
    request_by_id = {str(row["instance_id"]): row for row in requests}
    request_by_sif = {str(row["sif_filename"]): row for row in requests}
    if len(request_by_id) != 25 or len(request_by_sif) != 25:
        raise ValueError("frozen requests are not unique")

    original = json.loads(original_provenance.read_text(encoding="utf-8"))
    if original.get("summary") != {"available": 17, "failed": 8, "requested": 25}:
        raise ValueError("original provenance is not the expected 17/25 terminal run")
    initial_available = {
        image
        for image, record in (original.get("records") or {}).items()
        if isinstance(record, dict) and record.get("status") in {"cached", "pulled"}
    }
    if len(initial_available) != 17:
        raise ValueError("original provenance does not contain 17 available images")

    smoke_text = smoke_log.read_text(encoding="utf-8")
    recovery_text = recovery_log.read_text(encoding="utf-8")
    smoke = _single_smoke_record(smoke_text, request_by_sif)
    recovered, summary = _recovery_records(recovery_text, request_by_id, cache_dir)
    overlay_images = {smoke["requested_ref"], *(row["requested_ref"] for row in recovered)}
    if initial_available & overlay_images:
        raise ValueError("recovery overlay overlaps initially available images")
    all_requested = {str(row["image_ref"]) for row in requests}
    if initial_available | overlay_images != all_requested:
        raise ValueError("initial and recovery evidence do not cover quick25")

    value = {
        "schema_version": 1,
        "artifact_type": "swe_bench_pro_preheat_recovery_overlay",
        "selection_id": requests_value.get("selection_id"),
        "request_manifest_sha256": file_sha256(request_manifest),
        "original_provenance_sha256": file_sha256(original_provenance),
        "preserves_original_failure_provenance": True,
        "terminal_availability": {"available": 25, "failed": 0, "requested": 25},
        "runs": [
            {
                "job_id": smoke_job_id,
                "role": "single_image_node_tmp_smoke",
                "stdout_sha256": file_sha256(smoke_log),
                "records": [smoke],
            },
            {
                "job_id": recovery_job_id,
                "role": "all_missing_node_tmp_recovery",
                "stdout_sha256": file_sha256(recovery_log),
                "summary": summary,
                "records": recovered,
            },
        ],
    }
    _write_frozen(output, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--original-provenance", required=True, type=Path)
    parser.add_argument("--smoke-log", required=True, type=Path)
    parser.add_argument("--recovery-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--smoke-job-id", required=True)
    parser.add_argument("--recovery-job-id", required=True)
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()
    result = finalize(**vars(args))
    print(json.dumps(result["terminal_availability"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
