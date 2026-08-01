#!/usr/bin/env python3
"""Explicitly extend a completed Offline GEPA iteration target."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return _sha256_bytes(encoded)


def _atomic_json(path: Path, value: Any) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def extend_iteration_target(
    run_dir: Path,
    *,
    new_target: int,
    reason: str,
) -> dict[str, Any]:
    manifest_path = run_dir / "run_manifest.json"
    state_path = run_dir / "gepa_state.bin"
    resume_path = run_dir / "gepa_resume_state.json"
    progress_path = run_dir / "progress.json"
    result_path = run_dir / "result.json"
    required = (manifest_path, state_path, resume_path, progress_path, result_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"completed checkpoint is missing files: {missing}")

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    semantic = manifest.get("semantic_config")
    if not isinstance(semantic, dict):
        raise ValueError("run manifest has no semantic_config object")
    stored_semantic_hash = str(manifest.get("semantic_sha256", ""))
    if _semantic_sha256(semantic) != stored_semantic_hash:
        raise ValueError("stored semantic_sha256 does not match semantic_config")

    search = semantic.get("search")
    if not isinstance(search, dict) or search.get("max_iterations") is None:
        raise ValueError("run manifest has no finite max_iterations")
    old_target = int(search["max_iterations"])
    if new_target <= old_target:
        raise ValueError(
            f"new target must increase the stored target ({new_target} <= {old_target})"
        )

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("status") not in {"completed", "completed_with_warnings"}:
        raise ValueError("iteration target can be extended only after completion")
    resume = json.loads(resume_path.read_text(encoding="utf-8"))
    state_i = int(resume.get("gepa_state_i", -1))
    if state_i + 1 != old_target:
        raise ValueError(
            "checkpoint iteration does not equal the stored completed target "
            f"({state_i + 1} != {old_target})"
        )

    backup_path = run_dir / f"run_manifest.before_iteration_extension_{old_target}.json"
    if backup_path.exists():
        raise ValueError(f"manifest backup already exists: {backup_path}")
    backup_path.write_bytes(manifest_bytes)

    checkpoint_dir = run_dir / "iteration_checkpoints" / f"iteration_{old_target:04d}"
    if checkpoint_dir.exists():
        raise ValueError(f"iteration checkpoint already exists: {checkpoint_dir}")
    checkpoint_dir.mkdir(parents=True)
    report_names = (
        "result.json",
        "candidate_metrics.json",
        "cost_report.json",
        "best_rules.txt",
        "candidate_tree.html",
        "candidates.json",
        "progress.json",
        "iteration_progress.json",
        "controller_status.json",
    )
    for name in report_names:
        source = run_dir / name
        if source.is_file():
            shutil.copy2(source, checkpoint_dir / name)
    shutil.copy2(backup_path, checkpoint_dir / backup_path.name)

    search["max_iterations"] = new_target
    previous_manifest_sha256 = _sha256_bytes(manifest_bytes)
    previous_semantic_sha256 = stored_semantic_hash
    history = manifest.setdefault("iteration_target_extensions", [])
    if not isinstance(history, list):
        raise ValueError("iteration_target_extensions must be a list")
    history.append(
        {
            "from": old_target,
            "to": new_target,
            "additional_iterations": new_target - old_target,
            "reason": reason,
            "previous_manifest_sha256": previous_manifest_sha256,
            "previous_semantic_sha256": previous_semantic_sha256,
            "checkpoint_gepa_state_i": state_i,
            "checkpoint_sha256": _file_sha256(state_path),
            "backup_file": backup_path.name,
            "report_checkpoint_dir": str(checkpoint_dir.relative_to(run_dir)),
        }
    )
    manifest.setdefault("initial_max_iterations", old_target)
    manifest["latest_max_iterations"] = new_target
    manifest["semantic_sha256"] = _semantic_sha256(semantic)
    _atomic_json(manifest_path, manifest)
    # The supervisor treats a root result.json as terminal. Its immutable 8it
    # copy is retained above; removing only this derived terminal marker lets
    # the ordinary resume path collect the existing GEPA checkpoint.
    result_path.unlink()
    return {
        "run_dir": str(run_dir),
        "from": old_target,
        "to": new_target,
        "additional_iterations": new_target - old_target,
        "backup_file": backup_path.name,
        "report_checkpoint_dir": str(checkpoint_dir.relative_to(run_dir)),
        "previous_manifest_sha256": previous_manifest_sha256,
        "new_semantic_sha256": manifest["semantic_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--new-target", type=int, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.new_target < 1:
        raise SystemExit("--new-target must be positive")
    result = extend_iteration_target(
        args.run_dir,
        new_target=args.new_target,
        reason=args.reason,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
