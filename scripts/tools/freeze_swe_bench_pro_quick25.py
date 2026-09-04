#!/usr/bin/env python3
"""Freeze an outcome-independent SWE-bench Pro Python quick25 selection."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


SEED = "swe-bench-pro-python-quick25-v1-20260904"
DEFAULT_MATRIX = {
    "ansible/ansible": {"bug": 4, "feature": 2, "enhancement": 1, "mixed": 2},
    "internetarchive/openlibrary": {
        "bug": 2,
        "feature": 3,
        "enhancement": 3,
        "mixed": 1,
    },
    "qutebrowser/qutebrowser": {
        "bug": 2,
        "feature": 2,
        "enhancement": 2,
        "mixed": 1,
    },
}
VISIBLE_SELECTION_FIELDS = [
    "repo",
    "instance_id",
    "base_commit",
    "repo_language",
    "dockerhub_tag",
    "problem_statement",
    "requirements",
    "interface",
    "issue_specificity",
]


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rank(instance_id: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode()).hexdigest()


def _task_kind(raw: str) -> str:
    values = json.loads(raw)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"Invalid issue_specificity: {raw!r}")
    kinds = set()
    if any(value.endswith("_bug") for value in values):
        kinds.add("bug")
    if any(value.endswith("_feat") for value in values):
        kinds.add("feature")
    if any(value.endswith("_enh") for value in values):
        kinds.add("enhancement")
    if len(kinds) == 1:
        return kinds.pop()
    if len(kinds) > 1:
        return "mixed"
    raise ValueError(f"No supported task kind in issue_specificity: {raw!r}")


def _write_frozen(path: Path, value: Any) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"Refusing to overwrite different frozen content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def freeze_selection(
    *,
    parquet: Path,
    source_manifest_path: Path,
    request_manifest_path: Path,
    output_dir: Path,
    matrix: dict[str, dict[str, int]] = DEFAULT_MATRIX,
    seed: str = SEED,
) -> dict[str, Any]:
    source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    requests_source = json.loads(request_manifest_path.read_text(encoding="utf-8"))
    parquet_sha256 = _file_hash(parquet)
    if parquet_sha256 != source["source_parquet_sha256"]:
        raise ValueError("Parquet does not match the frozen source manifest")
    if requests_source["source_parquet_sha256"] != parquet_sha256:
        raise ValueError("Request manifest does not match the frozen Parquet")

    rows = pq.read_table(parquet, columns=VISIBLE_SELECTION_FIELDS).to_pylist()
    rows = [
        row
        for row in rows
        if str(row["repo_language"]).strip().casefold() == "python"
    ]
    if len(rows) != source["python_selection"]["count"]:
        raise ValueError("Python row count differs from the frozen source manifest")
    request_by_id = {
        item["instance_id"]: item for item in requests_source["requests"]
    }
    if len(request_by_id) != len(requests_source["requests"]):
        raise ValueError("Duplicate instance IDs in request manifest")

    required_text = ("problem_statement", "requirements", "interface")
    for row in rows:
        if any(not str(row[field] or "").strip() for field in required_text):
            raise ValueError(f"Incomplete official task text: {row['instance_id']}")
        request = request_by_id.get(str(row["instance_id"]))
        if not request:
            raise ValueError(f"Missing acquisition request: {row['instance_id']}")
        for field in ("repo", "base_commit", "dockerhub_tag"):
            if str(request[field]) != str(row[field]):
                raise ValueError(f"Request mismatch for {row['instance_id']}: {field}")

    selected: list[dict[str, Any]] = []
    source_bucket_counts: dict[str, dict[str, int]] = {}
    for repo, kind_quotas in matrix.items():
        source_bucket_counts[repo] = {}
        for kind, quota in kind_quotas.items():
            candidates = [
                row
                for row in rows
                if row["repo"] == repo and _task_kind(row["issue_specificity"]) == kind
            ]
            source_bucket_counts[repo][kind] = len(candidates)
            if len(candidates) < quota:
                raise ValueError(f"Insufficient {repo}/{kind} cases: {len(candidates)} < {quota}")
            candidates.sort(key=lambda row: _rank(str(row["instance_id"]), seed))
            for row in candidates[:quota]:
                selected.append(
                    {
                        "instance_id": str(row["instance_id"]),
                        "repo": repo,
                        "task_kind": kind,
                        "rank_sha256": _rank(str(row["instance_id"]), seed),
                        "selection_role": "repository_and_task_kind_proportional_stratum",
                    }
                )
    selected.sort(key=lambda item: (item["repo"], item["task_kind"], item["rank_sha256"]))
    selected_ids = [item["instance_id"] for item in selected]
    selected_requests = [request_by_id[instance_id] for instance_id in selected_ids]

    selection_manifest = {
        "schema_version": 1,
        "selection_id": "swe-bench-pro-python-quick25-v1-20260904",
        "purpose": "development PCE-PC-PCCE quick diagnostic; not a population estimate or untouched holdout",
        "dataset": source["dataset"],
        "dataset_revision": source["revision"],
        "source_manifest_sha256": _file_hash(source_manifest_path),
        "source_request_manifest_sha256": _file_hash(request_manifest_path),
        "eligible_instance_count": len(rows),
        "selection_uses_outcomes_plans_patches_or_tests": False,
        "official_task_rendering": "problem_statement + Requirements + New interfaces introduced",
        "selection_seed": seed,
        "selection_matrix": matrix,
        "source_bucket_counts": source_bucket_counts,
        "selected_instance_count": len(selected),
        "selected_cases": selected,
    }
    request_manifest = {
        "schema_version": 1,
        "purpose": "swe_bench_pro_python_quick25_sif_acquisition_requests",
        "selection_id": selection_manifest["selection_id"],
        "dataset_revision": source["revision"],
        "source_request_manifest_sha256": _file_hash(request_manifest_path),
        "request_count": len(selected_requests),
        "contains_solution_or_test_evidence": False,
        "requests": selected_requests,
    }
    preheat_images = {
        "schema_version": 1,
        "purpose": "direct_input_for_login_apptainer_sif_preheat",
        "selection_id": selection_manifest["selection_id"],
        "source_request_manifest_sha256": _file_hash(request_manifest_path),
        "images": [request["image_ref"] for request in selected_requests],
    }
    _write_frozen(output_dir / "selection-manifest.json", selection_manifest)
    _write_frozen(output_dir / "image-request-manifest.json", request_manifest)
    _write_frozen(output_dir / "preheat-images.json", preheat_images)
    return {
        "selection": selection_manifest,
        "requests": request_manifest,
        "preheat_images": preheat_images,
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--parquet", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--request-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = freeze_selection(
        parquet=args.parquet,
        source_manifest_path=args.source_manifest,
        request_manifest_path=args.request_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps({"selected": result["selection"]["selected_instance_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
