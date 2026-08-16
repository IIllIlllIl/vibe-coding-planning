"""Offline iteration-target transition used internally by the supervisor."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _semantic_sha256(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return _sha256_bytes(encoded)


def _atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".%s." % path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def extend_iteration_target(run_dir, new_target, reason):
    """Reopen a completed Offline run by increasing only its iteration target."""

    # The supervisor's default remote state root is expressed with ``~/``.
    # Unlike a path embedded directly in a shell command, this value arrives as
    # a quoted Python argument and therefore needs explicit user expansion.
    run_dir = Path(run_dir).expanduser()
    manifest_path = run_dir / "run_manifest.json"
    state_path = run_dir / "gepa_state.bin"
    resume_path = run_dir / "gepa_resume_state.json"
    progress_path = run_dir / "progress.json"
    result_path = run_dir / "result.json"
    required = (manifest_path, state_path, resume_path, progress_path, result_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("completed checkpoint is missing files: %s" % missing)

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
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
    if int(new_target) <= old_target:
        raise ValueError(
            "new target must increase the stored target (%s <= %s)"
            % (new_target, old_target)
        )

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    if progress.get("status") not in {"completed", "completed_with_warnings"}:
        raise ValueError("iteration target can be extended only after completion")
    resume = json.loads(resume_path.read_text(encoding="utf-8"))
    state_i = int(resume.get("gepa_state_i", -1))
    if state_i + 1 != old_target:
        raise ValueError(
            "checkpoint iteration does not equal the stored completed target "
            "(%s != %s)" % (state_i + 1, old_target)
        )

    backup_path = run_dir / (
        "run_manifest.before_iteration_extension_%s.json" % old_target
    )
    checkpoint_dir = run_dir / "iteration_checkpoints" / (
        "iteration_%04d" % old_target
    )
    if backup_path.exists():
        raise ValueError("manifest backup already exists: %s" % backup_path)
    if checkpoint_dir.exists():
        raise ValueError("iteration checkpoint already exists: %s" % checkpoint_dir)

    checkpoint_dir.mkdir(parents=True)
    backup_path.write_bytes(manifest_bytes)
    report_names = (
        "result.json",
        "candidate_metrics.json",
        "cost_report.json",
        "best_guideline.txt",
        # Historical checkpoints used this name; retain it when present.
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
            shutil.copy2(str(source), str(checkpoint_dir / name))
    shutil.copy2(str(backup_path), str(checkpoint_dir / backup_path.name))

    search["max_iterations"] = int(new_target)
    previous_manifest_sha256 = _sha256_bytes(manifest_bytes)
    history = manifest.setdefault("iteration_target_extensions", [])
    if not isinstance(history, list):
        raise ValueError("iteration_target_extensions must be a list")
    history.append(
        {
            "from": old_target,
            "to": int(new_target),
            "additional_iterations": int(new_target) - old_target,
            "reason": reason,
            "previous_manifest_sha256": previous_manifest_sha256,
            "previous_semantic_sha256": stored_semantic_hash,
            "checkpoint_gepa_state_i": state_i,
            "checkpoint_sha256": _file_sha256(state_path),
            "backup_file": backup_path.name,
            "report_checkpoint_dir": str(checkpoint_dir.relative_to(run_dir)),
        }
    )
    manifest.setdefault("initial_max_iterations", old_target)
    manifest["latest_max_iterations"] = int(new_target)
    manifest["semantic_sha256"] = _semantic_sha256(semantic)
    _atomic_json(manifest_path, manifest)
    result_path.unlink()
    return {
        "run_dir": str(run_dir),
        "from": old_target,
        "to": int(new_target),
        "additional_iterations": int(new_target) - old_target,
        "backup_file": backup_path.name,
        "report_checkpoint_dir": str(checkpoint_dir.relative_to(run_dir)),
        "previous_manifest_sha256": previous_manifest_sha256,
        "new_semantic_sha256": manifest["semantic_sha256"],
    }
