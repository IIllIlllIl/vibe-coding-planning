from __future__ import annotations

import argparse
import re
import shlex
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_FILE = REPO_ROOT / "output" / ".hpc_preheat_watchdog_state.json"
DEFAULT_PREHEAT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "submit_apptainer_sif_preheat.sh"
DEFAULT_ULHPC_CONFIG = REPO_ROOT / "configs" / "ulhpc_submit.yaml"


def parse_duration(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    if re.fullmatch(r"\d+h", raw):
        return int(raw[:-1]) * 3600
    if re.fullmatch(r"\d+m", raw):
        return int(raw[:-1]) * 60
    parts = raw.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        minutes, seconds = (int(part) for part in parts)
        return minutes * 60 + seconds
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        hours, minutes, seconds = (int(part) for part in parts)
        return hours * 3600 + minutes * 60 + seconds
    raise argparse.ArgumentTypeError(f"invalid duration: {raw}")


def resolve_repo_path(raw: str | Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def parse_command(raw: str) -> tuple[str, ...]:
    command = tuple(shlex.split(raw))
    if not command:
        raise argparse.ArgumentTypeError("command must not be empty")
    return command


@dataclass(frozen=True)
class WatchdogConfig:
    pilot_config: Path
    full_config: Path
    pilot_sif_cache_dir: str
    full_sif_cache_dir: str
    state_file: Path = DEFAULT_STATE_FILE
    preheat_script: Path = DEFAULT_PREHEAT_SCRIPT
    ulhpc_config: Path = DEFAULT_ULHPC_CONFIG
    remote_project_dir: str = "/scratch/users/twang/vibe-coding-planning/runs/vibe-sif-preheat-watchdog"
    remote_dataset_dir: str = "/scratch/users/twang/vibe-coding-planning/hpc_datasets"
    pilot_job_name: str = "gepa-preheat-pilot-watchdog"
    full_job_name: str = "gepa-preheat-full-watchdog"
    pilot_time: str = "00:30:00"
    full_time: str = "08:00:00"
    cpus: str = "1"
    mem: str = "4G"
    pull_timeout: str = "0"
    max_pull_attempts: str = "1"
    retry_backoff: str = "0"
    poll_interval_seconds: int = 3600
    ssh_retry_interval_seconds: int = 300
    ssh_retry_attempts: int = 3
    agent_cooldown_seconds: int = 18000
    max_repair_attempts: int = 6
    max_whitelist_violations: int = 2
    max_agent_cooldowns: int = 20
    submit: bool = False
    enable_agent_repair: bool = False
    stop_after_full_submit: bool = True
    agent_command: tuple[str, ...] = (
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
        "-C",
        str(REPO_ROOT),
    )

    @property
    def pilot_config_rel(self) -> str:
        return self.pilot_config.resolve().relative_to(REPO_ROOT.resolve()).as_posix()

    @property
    def full_config_rel(self) -> str:
        return self.full_config.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
