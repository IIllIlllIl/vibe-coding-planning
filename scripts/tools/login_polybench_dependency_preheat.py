#!/usr/bin/env python3
"""Serially prepare PolyBench evaluator caches on an Iris login node."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

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
    return r'''
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

payload = json.load(sys.stdin)
root = Path(payload["remote_cache_root"])
root.mkdir(parents=True, exist_ok=True)
state_path = root / "state.json"

def now():
    return datetime.now(timezone.utc).isoformat()

def save(state):
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state_path)

if state_path.is_file():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {
        "schema_version": 1,
        "status": "running",
        "created_at": now(),
        "config_sha256": payload["config_sha256"],
        "downloader_sha256": payload["downloader_sha256"],
        "instances": {},
    }
if state.get("config_sha256") != payload["config_sha256"]:
    raise SystemExit("existing dependency-preheat state has a different config hash")
if state.get("downloader_sha256") != payload["downloader_sha256"]:
    raise SystemExit("existing dependency-preheat state has a different downloader hash")

download_code = r"""
import inspect, json, os
from huggingface_hub import model_info, snapshot_download
repo = os.environ["VIBE_HF_REPO"]
profile = os.environ["VIBE_PROFILE"]
revision = model_info(repo).sha
kwargs = {"repo_id": repo, "revision": revision, "cache_dir": os.environ["VIBE_CACHE_DIR"]}
common = [
    "*.json", "**/*.json", "*.txt", "**/*.txt", "*.model", "**/*.model",
    "*.spm", "**/*.spm", "tokenizer*", "**/tokenizer*", "vocab*",
    "**/vocab*", "merges.txt", "**/merges.txt",
]
patterns = {
    "config": ["config.json", "generation_config.json", "preprocessor_config.json"],
    "tokenizer": common,
    "pytorch": common + [
        "*.safetensors", "**/*.safetensors", "pytorch_model*.bin",
        "**/pytorch_model*.bin", "*.py", "**/*.py",
    ],
    "flax": common + ["flax_model*.msgpack", "**/flax_model*.msgpack"],
}[profile]
if "allow_patterns" in inspect.signature(snapshot_download).parameters:
    kwargs["allow_patterns"] = patterns
if "max_workers" in inspect.signature(snapshot_download).parameters:
    kwargs["max_workers"] = 1
path = snapshot_download(**kwargs)
print(json.dumps({"repo_id": repo, "revision": revision, "profile": profile,
                  "snapshot_path": path}))
"""
verify_code = r"""
import inspect, json, os
from huggingface_hub import snapshot_download
repo = os.environ["VIBE_HF_REPO"]
revision = os.environ["VIBE_HF_REVISION"]
kwargs = {"repo_id": repo, "revision": revision, "local_files_only": True,
          "cache_dir": os.environ["VIBE_CACHE_DIR"]}
path = snapshot_download(**kwargs)
print(json.dumps({"repo_id": repo, "revision": revision, "snapshot_path": path}))
"""

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        chunk = handle.read(8 * 1024 * 1024)
        while chunk:
            digest.update(chunk)
            chunk = handle.read(8 * 1024 * 1024)
    return digest.hexdigest()

def apptainer_python(apptainer, sif, home, cache, code, env, *, offline):
    args = [apptainer, "exec", "--cleanenv", "--writable-tmpfs"]
    if offline:
        args.extend(["--net", "--network", "none"])
    args.extend(["--home", f"{home}:/tmp/vibe_home"])
    args.extend(["--bind", f"{cache}:/dependency-cache"])
    values = {
        "TRANSFORMERS_OFFLINE": "1" if offline else "0",
        "HF_HUB_OFFLINE": "1" if offline else "0",
        "HF_HOME": "/dependency-cache",
        "HF_HUB_CACHE": "/dependency-cache/hub",
        "HUGGINGFACE_HUB_CACHE": "/dependency-cache/hub",
        "TRANSFORMERS_CACHE": "/dependency-cache/hub",
        "VIBE_CACHE_DIR": "/dependency-cache/hub",
        **env,
    }
    for key, value in values.items():
        args.extend(["--env", f"{key}={value}"])
    args.extend([sif, "python", "-c", code])
    return subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, check=False,
        timeout=payload["artifact_timeout_seconds"],
    )

for item in payload["instances"]:
    instance_id = item["instance_id"]
    record = state["instances"].setdefault(
        instance_id,
        {"status": "running", "artifacts": {}, "sif_sha256": item["sif_sha256"],
         "profile": item["profile"]},
    )
    home = root / "instances" / instance_id / "home-cache"
    cache = root / "instances" / instance_id / "dependency-cache"
    home.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    sif = Path(item["sif_path"])
    if not sif.is_file():
        record.update(status="failed", error=f"missing SIF: {sif}", updated_at=now())
        save(state)
        continue
    if "observed_sif_sha256" not in record:
        record["observed_sif_sha256"] = sha256(sif)
        if record["observed_sif_sha256"] != item["sif_sha256"]:
            record.update(status="failed", error="SIF sha256 mismatch", updated_at=now())
            save(state)
            continue
        save(state)
    for repo in item["artifacts"]:
        artifact = record["artifacts"].setdefault(repo, {"status": "pending"})
        if artifact.get("status") in {"completed", "failed"}:
            continue
        artifact.update(status="downloading", started_at=now())
        save(state)
        try:
            result = apptainer_python(
                payload["remote_apptainer_bin"], str(sif), home, cache, download_code,
                {"VIBE_HF_REPO": repo, "VIBE_PROFILE": item["profile"]}, offline=False,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout)[-2000:])
            info = json.loads(result.stdout.strip().splitlines()[-1])
            verify = apptainer_python(
                payload["remote_apptainer_bin"], str(sif), home, cache, verify_code,
                {"VIBE_HF_REPO": repo, "VIBE_HF_REVISION": info["revision"]},
                offline=True,
            )
            if verify.returncode != 0:
                raise RuntimeError("offline verification failed: " + (verify.stderr or verify.stdout)[-2000:])
            artifact.update(
                status="completed", revision=info["revision"], completed_at=now()
            )
        except Exception as exc:
            artifact.update(
                status="failed", error=f"{type(exc).__name__}: {exc}", completed_at=now()
            )
        save(state)
        print(json.dumps({"event": "artifact_finished", "instance_id": instance_id,
                          "repo": repo, "status": artifact["status"]}), flush=True)
    statuses = [value["status"] for value in record["artifacts"].values()]
    record["status"] = "completed" if all(x == "completed" for x in statuses) else "completed_with_failures"
    record["updated_at"] = now()
    save(state)

instance_statuses = [value["status"] for value in state["instances"].values()]
state["status"] = "completed" if all(x == "completed" for x in instance_statuses) else "completed_with_failures"
state["completed_at"] = now()
save(state)
print(json.dumps({"event": "dependency_preheat_finished", "status": state["status"],
                  "state_path": str(state_path)}), flush=True)
'''


def _load(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("purpose") != "polybench_evaluator_dependency_preheat":
        raise ValueError("dependency preheat config has the wrong purpose")
    manifest_path = (REPO_ROOT / str(raw["image_manifest"])).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", {})
    instances: list[dict[str, Any]] = []
    profiles = {"config", "tokenizer", "pytorch", "flax"}
    for instance_id, spec in raw["instances"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"{instance_id}: expected profile/artifacts mapping")
        profile = str(spec.get("profile", ""))
        if profile not in profiles:
            raise ValueError(f"{instance_id}: unsupported profile {profile!r}")
        artifacts = spec.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError(f"{instance_id}: artifacts must be a non-empty list")
        matches = [value for key, value in records.items() if instance_id in key]
        if len(matches) != 1:
            raise ValueError(
                f"expected one frozen image for {instance_id}, got {len(matches)}"
            )
        image = matches[0]
        instances.append(
            {
                "instance_id": instance_id,
                "profile": profile,
                "artifacts": list(artifacts),
                "sif_path": image["sif_path"],
                "sif_sha256": image["sif_sha256"],
            }
        )
    return raw, instances


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ulhpc-config", type=Path, default=DEFAULT_ULHPC_CONFIG)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--remote-cache-root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    raw, instances = _load(config_path)
    if args.limit < 0:
        raise ValueError("--limit must be non-negative")
    if args.limit:
        instances = instances[: args.limit]
    import hashlib

    payload = {
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "downloader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "remote_cache_root": str(args.remote_cache_root or raw["remote_cache_root"]),
        "remote_apptainer_bin": str(raw["remote_apptainer_bin"]),
        "artifact_timeout_seconds": int(raw["artifact_timeout_seconds"]),
        "instances": instances,
    }
    print(
        json.dumps(
            {
                "event": "polybench_dependency_preheat_plan",
                "instances": len(instances),
                "artifact_requests": sum(len(x["artifacts"]) for x in instances),
                "remote_cache_root": payload["remote_cache_root"],
                "config_sha256": payload["config_sha256"],
                "downloader_sha256": payload["downloader_sha256"],
                "dry_run": args.dry_run,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0
    target, port, key = _ssh_config(args.ulhpc_config.resolve())
    command = "python3 -c " + shlex.quote(_remote_program())
    result = subprocess.run(
        _ssh_command(target, port, key, command),
        input=json.dumps(payload),
        text=True,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
