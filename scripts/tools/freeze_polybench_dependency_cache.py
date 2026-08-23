#!/usr/bin/env python3
"""Freeze a completed PolyBench dependency cache into a tracked manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tools.login_apptainer_sif_preheat import (  # noqa: E402
    DEFAULT_ULHPC_CONFIG,
    _ssh_command,
    _ssh_config,
)


def _remote_program() -> str:
    return r"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone

payload = json.load(sys.stdin)
root = Path(payload["remote_cache_root"])
state_path = root / "state.json"
state_bytes = state_path.read_bytes()
state = json.loads(state_bytes.decode("utf-8"))
if state.get("status") not in {"completed", "completed_with_failures"}:
    raise SystemExit("dependency-preheat state is not terminal")

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        chunk = handle.read(8 * 1024 * 1024)
        while chunk:
            digest.update(chunk)
            chunk = handle.read(8 * 1024 * 1024)
    return digest.hexdigest()

def inventory(cache):
    entries = []
    total_bytes = 0
    for base, dirs, files in os.walk(str(cache), followlinks=False):
        dirs.sort()
        files.sort()
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(cache).as_posix()
            info = os.lstat(str(path))
            if stat.S_ISLNK(info.st_mode):
                entries.append({"path": relative, "type": "symlink",
                                "target": os.readlink(str(path))})
            elif stat.S_ISREG(info.st_mode):
                total_bytes += info.st_size
                entries.append({"path": relative, "type": "file",
                                "bytes": info.st_size,
                                "sha256": sha256_file(path)})
            else:
                entries.append({"path": relative, "type": "other",
                                "mode": stat.S_IFMT(info.st_mode)})
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return entries, total_bytes, hashlib.sha256(canonical).hexdigest()

instances = {}
for instance_id, record in sorted(state["instances"].items()):
    cache = root / "instances" / instance_id / "dependency-cache"
    entries, total_bytes, inventory_sha = inventory(cache)
    artifacts = []
    for repo, artifact in sorted(record.get("artifacts", {}).items()):
        artifacts.append({"repo_id": repo, "status": artifact.get("status"),
                          "revision": artifact.get("revision"),
                          "error": artifact.get("error")})
    instances[instance_id] = {
        "status": record.get("status"),
        "profile": record.get("profile"),
        "sif_sha256": record.get("sif_sha256"),
        "observed_sif_sha256": record.get("observed_sif_sha256"),
        "artifacts": artifacts,
        "cache": {"file_entries": len(entries), "regular_file_bytes": total_bytes,
                  "inventory_sha256": inventory_sha, "files": entries},
    }

included = sorted(k for k, v in instances.items() if v["status"] == "completed")
excluded = []
for instance_id, value in instances.items():
    if value["status"] != "completed":
        failures = [a for a in value["artifacts"] if a["status"] != "completed"]
        excluded.append({"instance_id": instance_id,
                         "reason": "dependency_cache_incomplete",
                         "failed_artifacts": failures})

manifest = {
    "schema_version": 1,
    "purpose": "polybench_evaluator_dependency_cache_snapshot",
    "frozen_at": datetime.now(timezone.utc).isoformat(),
    "remote_cache_root": str(root),
    "source_state_sha256": hashlib.sha256(state_bytes).hexdigest(),
    "source_config_sha256": state.get("config_sha256"),
    "source_downloader_sha256": state.get("downloader_sha256"),
    "membership": {"included_instances": included,
                   "excluded_instances": excluded},
    "instances": instances,
}
print(json.dumps(manifest, indent=2, sort_keys=True))
"""


def _validate(
    manifest: dict[str, object],
    expected_root: str,
    *,
    expected_instances: set[str],
    expected_artifacts: int,
    expected_failed_artifacts: int,
    expected_excluded_instances: set[str],
) -> dict[str, int]:
    if manifest.get("purpose") != "polybench_evaluator_dependency_cache_snapshot":
        raise ValueError("remote output has the wrong purpose")
    if manifest.get("remote_cache_root") != expected_root:
        raise ValueError("remote cache root changed while freezing")
    instances = manifest.get("instances")
    if not isinstance(instances, dict) or set(instances) != expected_instances:
        raise ValueError("prepared instances differ from the frozen config")
    artifacts = [
        artifact
        for instance in instances.values()
        for artifact in instance["artifacts"]
    ]
    completed = [item for item in artifacts if item["status"] == "completed"]
    failed = [item for item in artifacts if item["status"] == "failed"]
    if len(artifacts) != expected_artifacts:
        raise ValueError("artifact request count differs from the frozen config")
    if len(failed) != expected_failed_artifacts:
        raise ValueError("failed artifact count differs from the frozen config")
    if len(completed) + len(failed) != len(artifacts):
        raise ValueError("an artifact has a non-terminal status")
    if any(not item.get("revision") for item in completed):
        raise ValueError("a completed artifact is missing its frozen revision")
    membership = manifest["membership"]
    excluded = {item["instance_id"] for item in membership["excluded_instances"]}
    if excluded != expected_excluded_instances:
        raise ValueError("excluded instances differ from the frozen config")
    included = set(membership["included_instances"])
    if included != expected_instances - expected_excluded_instances:
        raise ValueError("included instances differ from the frozen config")
    for instance in instances.values():
        if instance["sif_sha256"] != instance["observed_sif_sha256"]:
            raise ValueError("an observed SIF hash differs from its frozen hash")
    return {
        "included_instances": len(included),
        "excluded_instances": len(excluded),
        "completed_artifacts": len(completed),
    }


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ulhpc-config", type=Path, default=DEFAULT_ULHPC_CONFIG)
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    remote_root = str(config["remote_cache_root"])
    configured_instances = config.get("instances") or {}
    expected_instances = set(configured_instances)
    expected_artifacts = sum(
        len(instance.get("artifacts") or [])
        for instance in configured_instances.values()
    )
    expected_failed_artifacts = int(config.get("expected_failed_artifacts", 0))
    expected_excluded_instances = set(config.get("expected_excluded_instances") or [])
    target, port, key = _ssh_config(args.ulhpc_config.resolve())
    command = "python3 -c " + shlex.quote(_remote_program())
    result = subprocess.run(
        _ssh_command(target, port, key, command),
        input=json.dumps({"remote_cache_root": remote_root}),
        text=True,
        stdout=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    manifest = json.loads(result.stdout)
    counts = _validate(
        manifest,
        remote_root,
        expected_instances=expected_instances,
        expected_artifacts=expected_artifacts,
        expected_failed_artifacts=expected_failed_artifacts,
        expected_excluded_instances=expected_excluded_instances,
    )
    canonical = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    manifest_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(canonical)
    temporary.replace(output)
    print(
        json.dumps(
            {
                "event": "polybench_dependency_cache_frozen",
                "output": str(output),
                "manifest_sha256": manifest_sha,
                **counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
