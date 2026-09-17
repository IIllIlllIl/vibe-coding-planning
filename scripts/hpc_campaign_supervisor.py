#!/usr/bin/env python3
"""Supervise several independent HPC runs from one durable local process.

Each campaign member is one ordinary ``hpc_resume_loop`` invocation. The
campaign adds ``--once`` and revisits every non-terminal member once per poll
round. It does not merge run authorities, checkpoints, or Slurm arrays.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
RESUME_SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"
COMPLETE_MEMBER_STATUSES = {"completed", "iteration_target_reached"}
BLOCKED_MEMBER_STATUSES = {
    "completed_before_iteration_target",
    "max_runs_reached",
}


@dataclass(frozen=True)
class CampaignMember:
    member_id: str
    arguments: tuple[str, ...]
    state_file: Path


@dataclass(frozen=True)
class CampaignConfig:
    path: Path
    campaign_id: str
    poll_interval_seconds: int
    state_file: Path
    members: tuple[CampaignMember, ...]
    sha256: str


def _option(arguments: Sequence[str], name: str) -> str | None:
    for index, value in enumerate(arguments):
        if value == name:
            if index + 1 >= len(arguments):
                raise ValueError(f"{name} requires a value")
            return arguments[index + 1]
        prefix = f"{name}="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def _repo_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path


def load_campaign_config(path: Path) -> CampaignConfig:
    resolved = path if path.is_absolute() else REPO_ROOT / path
    raw_bytes = resolved.read_bytes()
    raw = yaml.safe_load(raw_bytes) or {}
    if raw.get("schema_version") != 1:
        raise ValueError("campaign config must use schema_version: 1")
    campaign_id = raw.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError("campaign_id must be a non-empty string")
    poll = raw.get("poll_interval_seconds", 300)
    if not isinstance(poll, int) or poll < 0:
        raise ValueError("poll_interval_seconds must be a non-negative integer")
    state_raw = raw.get("state_file")
    if not isinstance(state_raw, str) or not state_raw:
        raise ValueError("campaign state_file must be a non-empty string")
    member_values = raw.get("members")
    if not isinstance(member_values, list) or not member_values:
        raise ValueError("campaign requires at least one member")

    members: list[CampaignMember] = []
    seen_ids: set[str] = set()
    seen_states: set[Path] = set()
    for value in member_values:
        if not isinstance(value, dict):
            raise ValueError("campaign members must be mappings")
        member_id = value.get("id")
        arguments = value.get("arguments")
        if not isinstance(member_id, str) or not member_id:
            raise ValueError("campaign member id must be a non-empty string")
        if member_id in seen_ids:
            raise ValueError(f"duplicate campaign member id: {member_id}")
        if not isinstance(arguments, list) or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise ValueError(f"campaign member {member_id} arguments must be strings")
        if "--submit" not in arguments:
            raise ValueError(f"campaign member {member_id} must explicitly submit")
        if "--once" in arguments:
            raise ValueError("campaign owns the --once transport boundary")
        member_state_raw = _option(arguments, "--state-file")
        if member_state_raw is None:
            raise ValueError(f"campaign member {member_id} requires --state-file")
        member_state = _repo_path(member_state_raw)
        if member_state in seen_states:
            raise ValueError(f"duplicate campaign member state file: {member_state}")
        seen_ids.add(member_id)
        seen_states.add(member_state)
        members.append(
            CampaignMember(
                member_id=member_id,
                arguments=tuple(arguments),
                state_file=member_state,
            )
        )

    return CampaignConfig(
        path=resolved,
        campaign_id=campaign_id,
        poll_interval_seconds=poll,
        state_file=_repo_path(state_raw),
        members=tuple(members),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON state must be an object: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _is_terminal(status: str) -> bool:
    return (
        status in COMPLETE_MEMBER_STATUSES
        or status in BLOCKED_MEMBER_STATUSES
        or status.startswith("blocked")
    )


def _load_campaign_state(config: CampaignConfig) -> dict[str, Any]:
    state = _read_json(config.state_file)
    identity = {
        "schema_version": 1,
        "campaign_id": config.campaign_id,
        "campaign_config_sha256": config.sha256,
        "members": [member.member_id for member in config.members],
    }
    if not state:
        return {
            **identity,
            "status": "prepared",
            "rounds": 0,
            "member_statuses": {},
        }
    for key, expected in identity.items():
        if state.get(key) != expected:
            raise RuntimeError(
                f"campaign state identity differs at {key}; use a new state file"
            )
    return state


def run_campaign(
    config: CampaignConfig,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    lock_path = config.state_file.with_suffix(config.state_file.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another campaign supervisor holds {lock_path}") from exc

        state = _load_campaign_state(config)
        while True:
            member_statuses: dict[str, str] = {}
            for member in config.members:
                member_state = _read_json(member.state_file)
                prior_status = str(member_state.get("status", "missing"))
                if not _is_terminal(prior_status):
                    command = [
                        sys.executable,
                        str(RESUME_SCRIPT),
                        *member.arguments,
                        "--once",
                    ]
                    print(
                        f"[hpc-campaign] member={member.member_id} step",
                        flush=True,
                    )
                    result = runner(
                        command,
                        cwd=REPO_ROOT,
                        text=True,
                        check=False,
                    )
                    member_state = _read_json(member.state_file)
                    if result.returncode != 0 and not member_state:
                        member_state = {"status": "invocation_failed_without_state"}
                member_statuses[member.member_id] = str(
                    member_state.get("status", "missing")
                )

            state["rounds"] = int(state.get("rounds", 0)) + 1
            state["member_statuses"] = member_statuses
            terminal = {
                member_id: status
                for member_id, status in member_statuses.items()
                if _is_terminal(status)
            }
            if len(terminal) == len(config.members):
                all_complete = all(
                    status in COMPLETE_MEMBER_STATUSES
                    for status in member_statuses.values()
                )
                state["status"] = (
                    "completed" if all_complete else "completed_with_blocked_members"
                )
                _atomic_json(config.state_file, state)
                return 0 if all_complete else 1

            state["status"] = "running"
            _atomic_json(config.state_file, state)
            if config.poll_interval_seconds:
                sleeper(config.poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Supervise independent HPC run authorities as one campaign."
    )
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    return run_campaign(load_campaign_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
