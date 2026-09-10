"""Apply an explicit, narrowly-scoped budget extension to a Playbook run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import yaml


ALLOWED_PREFIXES = {
    ("search", "max_iterations"),
    ("search", "max_metric_calls"),
    ("budget",),
    ("stopping", "stop_after_candidate_proposals"),
    ("success_criteria",),
    ("readiness",),
    ("resume",),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _flatten(value: object, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], object]:
    if isinstance(value, dict):
        result: dict[tuple[str, ...], object] = {}
        for key, child in value.items():
            result.update(_flatten(child, (*prefix, str(key))))
        return result
    return {prefix: value}


def _allowed(path: tuple[str, ...]) -> bool:
    return any(path[: len(prefix)] == prefix for prefix in ALLOWED_PREFIXES)


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def extend(run_dir: Path, old_config: Path, new_config: Path) -> dict[str, object]:
    old_bytes = old_config.read_bytes()
    new_bytes = new_config.read_bytes()
    old = yaml.safe_load(old_bytes) or {}
    new = yaml.safe_load(new_bytes) or {}
    old_flat, new_flat = _flatten(old), _flatten(new)
    changed = sorted(
        path for path in old_flat.keys() | new_flat.keys()
        if old_flat.get(path) != new_flat.get(path)
    )
    forbidden = [".".join(path) for path in changed if not _allowed(path)]
    if forbidden:
        raise ValueError("budget extension changes frozen semantics: " + ", ".join(forbidden))
    old_iterations = int(old["search"]["max_iterations"])
    new_iterations = int(new["search"]["max_iterations"])
    old_calls = int(old["search"]["max_metric_calls"])
    new_calls = int(new["search"]["max_metric_calls"])
    if new_iterations <= old_iterations or new_calls < old_calls:
        raise ValueError("budget extension must increase iterations and not decrease calls")

    manifest_path = run_dir.expanduser() / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic = manifest["semantic_config"]
    old_hash, new_hash = _sha256(old_bytes), _sha256(new_bytes)
    current_hash = semantic.get("runtime_config")
    history = manifest.setdefault("budget_extensions", [])
    if current_hash == new_hash:
        return {"status": "already_extended", "from": old_iterations, "to": new_iterations}
    if current_hash != old_hash:
        raise ValueError("manifest runtime config does not match the declared predecessor")
    semantic["runtime_config"] = new_hash
    manifest["semantic_sha256"] = _sha256(
        json.dumps(semantic, sort_keys=True).encode()
    )
    history.append({
        "from_iterations": old_iterations,
        "to_iterations": new_iterations,
        "from_metric_calls": old_calls,
        "to_metric_calls": new_calls,
        "old_runtime_config_sha256": old_hash,
        "new_runtime_config_sha256": new_hash,
        "changed_paths": [".".join(path) for path in changed],
    })
    _atomic_json(manifest_path, manifest)
    return {"status": "extended", "from": old_iterations, "to": new_iterations}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--old-config", required=True, type=Path)
    parser.add_argument("--new-config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(extend(args.run_dir, args.old_config, args.new_config), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
