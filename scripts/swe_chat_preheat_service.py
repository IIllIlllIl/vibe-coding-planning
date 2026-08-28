#!/usr/bin/env python3
"""Run the SWE-chat login preheater in a durable local tmux session."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PREHEAT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "login_swe_chat_preheat.py"


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def _has_session(session: str) -> bool:
    return _run(["tmux", "has-session", "-t", session]).returncode == 0


def _load_config(path: Path) -> tuple[str, Path, Path]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("schema_version") != 1 or raw.get("purpose") != "swe_chat_login_preheat":
        raise ValueError("SWE-chat preheat config has the wrong schema or purpose")
    supervisor = raw.get("supervisor") or {}
    session = supervisor.get("session")
    log = supervisor.get("log")
    if not isinstance(session, str) or not session:
        raise ValueError("supervisor.session must be a non-empty string")
    if not isinstance(log, str) or not log:
        raise ValueError("supervisor.log must be a non-empty string")
    ulhpc = raw.get("operational", {}).get("ulhpc_config", "configs/ulhpc_submit.yaml")
    log_path = Path(log).expanduser()
    if not log_path.is_absolute():
        log_path = REPO_ROOT / log_path
    ulhpc_path = Path(str(ulhpc)).expanduser()
    if not ulhpc_path.is_absolute():
        ulhpc_path = REPO_ROOT / ulhpc_path
    return session, log_path.resolve(), ulhpc_path.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--config", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    session, log_path, ulhpc_path = _load_config(config_path)
    if args.action == "status":
        running = _has_session(session)
        print("running" if running else "stopped")
        return 0 if running else 1
    if args.action == "stop":
        if not _has_session(session):
            print("already stopped")
            return 0
        return _run(["tmux", "kill-session", "-t", session]).returncode
    if _has_session(session):
        print(f"supervisor session already exists: {session}")
        return 1
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "exec",
        "caffeinate",
        "-i",
        "-s",
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "mini-swe",
        "python",
        str(PREHEAT_SCRIPT),
        "--config",
        str(config_path),
        "--ulhpc-config",
        str(ulhpc_path),
        "--run-until-terminal",
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
    result = _run(["tmux", "new-session", "-d", "-s", session, shell_command])
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="")
        return result.returncode
    print(f"started session={session} log={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
