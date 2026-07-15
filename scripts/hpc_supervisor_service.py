#!/usr/bin/env python3
"""Run the HPC resume supervisor in a durable tmux+caffeinate session."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
RESUME_SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _has_session(session: str) -> bool:
    return _run(["tmux", "has-session", "-t", session]).returncode == 0


def _load_launch_config(path: Path) -> tuple[str, str, list[str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("schema_version") != 1:
        raise ValueError("supervisor launch config must use schema_version: 1")
    session = data.get("session")
    log = data.get("log")
    arguments = data.get("arguments")
    if not isinstance(session, str) or not session:
        raise ValueError("supervisor launch config requires a non-empty session")
    if not isinstance(log, str) or not log:
        raise ValueError("supervisor launch config requires a non-empty log")
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) for argument in arguments
    ):
        raise ValueError("supervisor launch config arguments must be strings")
    return session, log, arguments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start, inspect, or stop a durable HPC resume supervisor."
    )
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--launch-config", type=Path)
    parser.add_argument("--session")
    parser.add_argument("--log")
    known, resume_args = parser.parse_known_args(argv)

    session = known.session
    log = known.log
    if known.launch_config is not None:
        if session is not None or log is not None or resume_args:
            parser.error(
                "--launch-config cannot be combined with --session, --log, "
                "or inline resume arguments"
            )
        launch_path = (
            known.launch_config
            if known.launch_config.is_absolute()
            else REPO_ROOT / known.launch_config
        )
        try:
            session, log, resume_args = _load_launch_config(launch_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            parser.error(str(exc))
    if not session:
        parser.error("--session or --launch-config is required")

    if known.action == "status":
        print("running" if _has_session(session) else "stopped")
        return 0 if _has_session(session) else 1
    if known.action == "stop":
        if not _has_session(session):
            print("already stopped")
            return 0
        return _run(["tmux", "kill-session", "-t", session]).returncode
    if not log:
        parser.error("start requires --log or --launch-config")
    if not resume_args:
        parser.error("start requires hpc_resume_loop arguments")
    if _has_session(session):
        print(f"supervisor session already exists: {session}", file=sys.stderr)
        return 1

    log_path = Path(log).expanduser()
    if not log_path.is_absolute():
        log_path = REPO_ROOT / log_path
    log_path = log_path.resolve()
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
        ["tmux", "new-session", "-d", "-s", session, shell_command]
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    print(f"started session={session} log={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
