#!/usr/bin/env python3
"""Start or inspect the bounded SWE-bench Pro quick25 SIF preheater."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PREHEAT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "login_apptainer_sif_preheat.py"


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def _has_session(session: str) -> bool:
    return _run(["tmux", "has-session", "-t", session]).returncode == 0


def _resolve(path: str) -> Path:
    value = Path(path).expanduser()
    return (value if value.is_absolute() else REPO_ROOT / value).resolve()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_config(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("schema_version") != 1 or raw.get("purpose") != "swe_bench_pro_sif_preheat":
        raise ValueError("Pro preheat config has the wrong schema or purpose")
    semantic = raw.get("semantic") or {}
    operational = raw.get("operational") or {}
    supervisor = raw.get("supervisor") or {}
    manifest = _resolve(str(semantic.get("preheat_images", "")))
    if not manifest.is_file():
        raise ValueError(f"preheat image manifest not found: {manifest}")
    expected_hash = str(semantic.get("preheat_images_sha256", ""))
    if _sha256(manifest) != expected_hash:
        raise ValueError("preheat image manifest SHA-256 mismatch")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    images = value.get("images") if isinstance(value, dict) else None
    expected_count = int(semantic.get("expected_images", 0))
    if (
        not isinstance(images, list)
        or not all(isinstance(image, str) for image in images)
        or len(images) != expected_count
        or len(images) != len(set(images))
    ):
        raise ValueError("preheat image manifest count or uniqueness mismatch")
    if not all(image.startswith("jefzda/sweap-images:") for image in images):
        raise ValueError("preheat manifest contains a non-Pro image")
    if operational.get("failure_policy") != "skip_and_report":
        raise ValueError("operational.failure_policy must be skip_and_report")
    for field in ("sif_cache_dir", "apptainer_cache_dir", "apptainer_tmp_dir"):
        if not str(operational.get(field, "")).startswith("/scratch/"):
            raise ValueError(f"operational.{field} must be an absolute scratch path")
    session = str(supervisor.get("session", ""))
    log = str(supervisor.get("log", ""))
    if not session or not log:
        raise ValueError("supervisor session and log are required")
    return {
        "manifest": manifest,
        "operational": operational,
        "session": session,
        "log": _resolve(log),
    }


def _preheat_command(plan: dict[str, Any]) -> list[str]:
    op = plan["operational"]
    command = [
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "mini-swe",
        "python",
        str(PREHEAT_SCRIPT),
        "--images-json",
        str(plan["manifest"]),
        "--ulhpc-config",
        str(_resolve(str(op.get("ulhpc_config", "configs/ulhpc_submit.yaml")))),
        "--sif-cache-dir",
        str(op["sif_cache_dir"]),
        "--apptainer-bin",
        str(op["apptainer_bin"]),
        "--apptainer-cache-dir",
        str(op["apptainer_cache_dir"]),
        "--apptainer-tmp-dir",
        str(op["apptainer_tmp_dir"]),
        "--timeout",
        str(op["pull_timeout_seconds"]),
        "--max-attempts",
        str(op["max_attempts"]),
        "--retry-backoff",
        str(op["retry_backoff_seconds"]),
        "--failed-output",
        str(op["failed_output"]),
        "--provenance-output",
        str(op["provenance_output"]),
        "--lock-file",
        str(op["lock_file"]),
        "--missing-only",
        "--cleanup-tmp",
    ]
    if bool(op.get("cleanup_apptainer_cache", False)):
        command.append("--cleanup-apptainer-cache")
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("action", choices=("start", "status", "stop", "dry-run"))
    parser.add_argument("--config", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = _load_config(args.config.resolve())
    session = plan["session"]
    if args.action == "status":
        running = _has_session(session)
        print("running" if running else "stopped")
        return 0 if running else 1
    if args.action == "stop":
        if not _has_session(session):
            print("already stopped")
            return 0
        return _run(["tmux", "kill-session", "-t", session]).returncode
    command = _preheat_command(plan)
    if args.action == "dry-run":
        print(json.dumps({"session": session, "command": command}, sort_keys=True))
        return 0
    if _has_session(session):
        print(f"supervisor session already exists: {session}")
        return 1
    plan["log"].parent.mkdir(parents=True, exist_ok=True)
    shell_command = (
        "cd "
        + shlex.quote(str(REPO_ROOT))
        + " && exec caffeinate -i -s "
        + " ".join(shlex.quote(part) for part in command)
        + " >> "
        + shlex.quote(str(plan["log"]))
        + " 2>&1"
    )
    result = _run(["tmux", "new-session", "-d", "-s", session, shell_command])
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="")
        return result.returncode
    print(f"started session={session} log={plan['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
