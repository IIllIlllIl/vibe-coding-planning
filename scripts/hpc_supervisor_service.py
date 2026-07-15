#!/usr/bin/env python3
"""Run the HPC resume supervisor in a durable tmux+caffeinate session."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
RESUME_SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _has_session(session: str) -> bool:
    return _run(["tmux", "has-session", "-t", session]).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start, inspect, or stop a durable HPC resume supervisor."
    )
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--session", required=True)
    parser.add_argument("--log", required=True)
    known, resume_args = parser.parse_known_args(argv)

    if known.action == "status":
        print("running" if _has_session(known.session) else "stopped")
        return 0 if _has_session(known.session) else 1
    if known.action == "stop":
        if not _has_session(known.session):
            print("already stopped")
            return 0
        return _run(["tmux", "kill-session", "-t", known.session]).returncode
    if not resume_args:
        parser.error("start requires hpc_resume_loop arguments")
    if _has_session(known.session):
        print(f"supervisor session already exists: {known.session}", file=sys.stderr)
        return 1

    log_path = Path(known.log).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "exec",
        "caffeinate",
        "-i",
        "-s",
        "conda",
        "run",
        "-n",
        "mini-swe",
        "python",
        str(RESUME_SCRIPT),
        *resume_args,
    ]
    shell_command = (
        "cd "
        + shlex.quote(str(REPO_ROOT))
        + " && "
        + " ".join(shlex.quote(part) for part in command)
        + " >> "
        + shlex.quote(str(log_path))
        + " 2>&1"
    )
    result = _run(
        ["tmux", "new-session", "-d", "-s", known.session, shell_command]
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    print(f"started session={known.session} log={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
