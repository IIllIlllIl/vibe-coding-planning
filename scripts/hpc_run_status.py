#!/usr/bin/env python3
"""Read-only status summary for a persistent ULHPC workflow run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ULHPC_CONFIG = REPO_ROOT / "configs" / "ulhpc_submit.yaml"
DEFAULT_REMOTE_RUN_ROOT = "~/hpc_run_state/vibe-coding-planning"

REMOTE_SCRIPT = r"""
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

run_dir = Path(os.path.expanduser(sys.argv[1]))
user = sys.argv[2]

def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"invalid_type": type(value).__name__}
    except Exception as exc:
        return {"read_error": str(exc)}

def job_state(job_id):
    queued = subprocess.run(
        ["squeue", "-h", "-j", str(job_id), "-o", "%T"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if queued.returncode == 0 and queued.stdout.strip():
        return queued.stdout.strip().splitlines()[0].split()[0]
    accounted = subprocess.run(
        ["sacct", "-X", "-n", "-P", "-j", str(job_id), "--format=State"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if accounted.returncode == 0 and accounted.stdout.strip():
        return accounted.stdout.strip().splitlines()[0].split("|", 1)[0].split()[0]
    return "UNKNOWN"

payload = {
    "run_dir": str(run_dir),
    "exists": run_dir.is_dir(),
    "result": None,
    "controller_status": None,
    "batches": [],
    "totals": {},
    "user_queue": [],
}
if run_dir.is_dir():
    if (run_dir / "result.json").is_file():
        payload["result"] = read_json(run_dir / "result.json")
    if (run_dir / "controller_status.json").is_file():
        payload["controller_status"] = read_json(run_dir / "controller_status.json")
    totals = Counter()
    for state_path in sorted((run_dir / "hpc_tasks").glob("**/task_state.json")):
        state = read_json(state_path)
        batch_dir = state_path.parent
        tasks = len(list((batch_dir / "tasks").glob("task_*.json")))
        outputs = len(list((batch_dir / "outputs").glob("task_*.json")))
        failures = len(list((batch_dir / "attempts").glob("task_*/attempt_*/failure.json")))
        active_job = state.get("active_job_id")
        item = {
            "path": str(batch_dir.relative_to(run_dir)),
            "phase": state.get("phase"),
            "active_attempt": state.get("active_attempt"),
            "active_job_id": active_job,
            "active_job_state": job_state(active_job) if active_job else None,
            "tasks": tasks,
            "outputs": outputs,
            "failure_records": failures,
        }
        payload["batches"].append(item)
        totals["batches"] += 1
        totals["tasks"] += tasks
        totals["outputs"] += outputs
        totals["failure_records"] += failures
        totals["batch_phase_" + str(state.get("phase", "UNKNOWN"))] += 1
    payload["totals"] = dict(sorted(totals.items()))

queue = subprocess.run(
    ["squeue", "-u", user, "-h", "-o", "%i|%T|%j|%M|%L|%R"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
)
if queue.returncode == 0:
    payload["user_queue"] = [line for line in queue.stdout.splitlines() if line.strip()]
print(json.dumps(payload, sort_keys=True))
"""


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return value


def _run_relative(config_path: Path) -> str:
    raw = _load_yaml(config_path)
    paths = raw.get("paths")
    if not isinstance(paths, dict) or not paths.get("run_dir"):
        raise ValueError(f"config has no paths.run_dir: {config_path}")
    candidate = Path(os.path.expandvars(str(paths["run_dir"]))).expanduser()
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    try:
        return candidate.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("paths.run_dir must be inside the repository") from exc


def _ssh_identity(ulhpc_config: Path) -> tuple[str, str, str]:
    raw = _load_yaml(ulhpc_config)
    host = os.environ.get("ULHPC_HOST") or str(raw.get("host", "access-iris.uni.lu"))
    port = os.environ.get("ULHPC_PORT") or str(raw.get("port", "8022"))
    user = os.environ.get("ULHPC_USER") or str(raw.get("user", ""))
    key = os.environ.get("ULHPC_SSH_KEY") or str(raw.get("ssh_key", ""))
    if not user:
        raise ValueError("ULHPC user is unavailable")
    return f"{user}@{host}", port, key


def _remote_run_path(config_path: Path, remote_root: str, repair_id: str) -> str:
    path = f"{remote_root.rstrip('/')}/{_run_relative(config_path)}"
    if repair_id:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", repair_id):
            raise ValueError("repair_id must match [A-Za-z0-9_.-]+")
        path += f"/evaluator_repairs/{repair_id}"
    return path


def query(
    *,
    config_path: Path,
    ulhpc_config: Path,
    remote_root: str,
    repair_id: str,
) -> dict[str, Any]:
    target, port, key = _ssh_identity(ulhpc_config)
    user = target.split("@", 1)[0]
    remote_path = _remote_run_path(config_path, remote_root, repair_id)
    command = ["ssh", "-p", port]
    if key:
        command.extend(["-i", os.path.expanduser(key)])
    command.extend(
        [
            target,
            "python3 -c "
            + shlex.quote(REMOTE_SCRIPT)
            + " "
            + shlex.quote(remote_path)
            + " "
            + shlex.quote(user),
        ]
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(f"remote status query failed with rc={result.returncode}")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--repair-id", default="")
    parser.add_argument("--remote-run-root", default=DEFAULT_REMOTE_RUN_ROOT)
    parser.add_argument("--ulhpc-config", type=Path, default=DEFAULT_ULHPC_CONFIG)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    ulhpc_config = (
        args.ulhpc_config
        if args.ulhpc_config.is_absolute()
        else REPO_ROOT / args.ulhpc_config
    )
    try:
        payload = query(
            config_path=config_path,
            ulhpc_config=ulhpc_config,
            remote_root=args.remote_run_root,
            repair_id=args.repair_id,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=None if args.compact else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
