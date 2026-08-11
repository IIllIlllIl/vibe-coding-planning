"""Freeze selected Offline GEPA candidates as an immutable evaluation bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "output/SWE-PolyBench/polybench-guideline-validation-guidelines"
)
SELECTED_INDICES = (0, 1, 2, 3)
LABELS = ("seed", "candidate_1", "candidate_2", "candidate_3")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def freeze_guideline_bundle(
    *,
    candidates_path: Path,
    source_manifest_path: Path,
    source_progress_path: Path,
    output_root: Path,
    source_run_id: str,
    source_artifact_root: str,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_progress = json.loads(source_progress_path.read_text(encoding="utf-8"))
    if not isinstance(candidates, list) or len(candidates) <= max(SELECTED_INDICES):
        raise ValueError("candidate source does not contain seed and candidates 1-3")
    if source_progress.get("status") != "completed":
        raise ValueError("source Offline run is not completed")

    selected = []
    for label, index in zip(LABELS, SELECTED_INDICES, strict=True):
        value = candidates[index]
        if not isinstance(value, dict) or not isinstance(value.get("rules"), str):
            raise ValueError(f"candidate {index} has no guideline text")
        text = value["rules"]
        selected.append(
            {
                "label": label,
                "source_candidate_index": index,
                "guideline": text,
                "guideline_sha256": _sha256_bytes(text.encode()),
            }
        )

    identity_material = json.dumps(
        [
            {
                "label": item["label"],
                "source_candidate_index": item["source_candidate_index"],
                "guideline_sha256": item["guideline_sha256"],
            }
            for item in selected
        ],
        sort_keys=True,
    ).encode()
    digest = _sha256_bytes(identity_material)
    timestamp = created_at or datetime.now(timezone.utc)
    bundle_id = f"{timestamp:%Y%m%d}_seed-c1-c2-c3_{digest[:12]}"
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / bundle_id
    if target.exists():
        existing = json.loads((target / "manifest.json").read_text())
        if existing.get("content_sha256") != digest:
            raise ValueError(f"guideline bundle identity collision: {target}")
        return existing

    build_dir = Path(tempfile.mkdtemp(prefix=".building-", dir=output_root))
    try:
        for item in selected:
            # Preserve the exact GEPA string: no implicit trailing newline.
            (build_dir / f"{item['label']}.md").write_text(
                item["guideline"], encoding="utf-8"
            )
        _write_json(build_dir / "guidelines.json", selected)
        manifest = {
            "schema_version": 1,
            "complete": True,
            "immutable": True,
            "bundle_id": bundle_id,
            "bundle_path": _portable_path(target),
            "created_at": timestamp.isoformat(),
            "content_sha256": digest,
            "source_run_id": source_run_id,
            "source_run_status": source_progress["status"],
            "source_completed_iterations": source_progress.get("iteration"),
            "source_best_candidate_idx": source_progress.get("best_candidate_idx"),
            "source_semantic_sha256": source_manifest.get("semantic_sha256"),
            "source_artifact_root": source_artifact_root,
            "source_candidates_artifact": f"{source_artifact_root}/candidates.json",
            "source_run_manifest_artifact": f"{source_artifact_root}/run_manifest.json",
            "source_progress_artifact": f"{source_artifact_root}/progress.json",
            "source_candidates_sha256": _sha256_path(candidates_path),
            "source_run_manifest_sha256": _sha256_path(source_manifest_path),
            "source_progress_sha256": _sha256_path(source_progress_path),
            "selected": [
                {
                    "label": item["label"],
                    "source_candidate_index": item["source_candidate_index"],
                    "guideline_sha256": item["guideline_sha256"],
                    "path": f"{item['label']}.md",
                }
                for item in selected
            ],
            "guidelines_sha256": _sha256_path(build_dir / "guidelines.json"),
        }
        _write_json(build_dir / "manifest.json", manifest)
        build_dir.replace(target)

        index_path = output_root / "index.json"
        index = {"schema_version": 1, "bundles": []}
        if index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        bundles = [
            item
            for item in index.get("bundles", [])
            if item.get("bundle_id") != bundle_id
        ]
        bundles.append(
            {
                "bundle_id": bundle_id,
                "bundle_path": manifest["bundle_path"],
                "content_sha256": digest,
                "source_run_id": source_run_id,
            }
        )
        _write_json(
            index_path,
            {
                "schema_version": 1,
                "active_bundle_id": bundle_id,
                "bundles": bundles,
            },
        )
        return manifest
    except BaseException:
        if build_dir.exists():
            shutil.rmtree(build_dir)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--source-run-manifest", required=True)
    parser.add_argument("--source-progress", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-artifact-root", required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    args = parser.parse_args()
    manifest = freeze_guideline_bundle(
        candidates_path=Path(args.candidates).resolve(),
        source_manifest_path=Path(args.source_run_manifest).resolve(),
        source_progress_path=Path(args.source_progress).resolve(),
        output_root=Path(args.output_root).resolve(),
        source_run_id=args.source_run_id,
        source_artifact_root=args.source_artifact_root.rstrip("/"),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
