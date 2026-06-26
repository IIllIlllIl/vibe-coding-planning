from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scripts.hpc_preheat_watchdog_lib.command import run_command


TERMINAL_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "COMPLETED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "SPECIAL_EXIT",
    "TIMEOUT",
}


@dataclass(frozen=True)
class SlurmConfig:
    ssh_target: str
    ssh_port: str
    ssh_key: str


def load_slurm_config(ulhpc_config: Path) -> SlurmConfig:
    data: dict[str, Any] = {}
    if ulhpc_config.is_file():
        data = yaml.safe_load(ulhpc_config.read_text(encoding="utf-8")) or {}
    host = os.environ.get("ULHPC_HOST") or str(data.get("host", "access-iris.uni.lu"))
    port = os.environ.get("ULHPC_PORT") or str(data.get("port", "8022"))
    user = os.environ.get("ULHPC_USER") or str(data.get("user", ""))
    ssh_key = os.environ.get("ULHPC_SSH_KEY") or str(data.get("ssh_key", ""))
    if not user:
        raise SystemExit("cannot determine ULHPC user; set configs/ulhpc_submit.yaml user")
    return SlurmConfig(ssh_target=f"{user}@{host}", ssh_port=port, ssh_key=ssh_key)


def state_base(raw: str) -> str:
    return raw.strip().split("|", 1)[0].split()[0].split("+", 1)[0]


def is_terminal(state: str | None) -> bool:
    return bool(state) and state_base(state) in TERMINAL_STATES


class SlurmClient:
    def __init__(self, config: SlurmConfig):
        self.config = config

    def ssh_command(self, remote_command: str) -> list[str]:
        command = ["ssh", "-p", self.config.ssh_port]
        if self.config.ssh_key:
            command.extend(["-i", os.path.expanduser(self.config.ssh_key)])
        command.extend([self.config.ssh_target, remote_command])
        return command

    def run_ssh(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        return run_command(self.ssh_command(remote_command))

    def get_job_state(self, job_id: str) -> str:
        squeue = "squeue -h -j " + shlex.quote(job_id) + " -o %T | head -n 1"
        result = self.run_ssh(squeue)
        if result.returncode == 0 and result.stdout.strip():
            return state_base(result.stdout)
        sacct = (
            "sacct -n -P -j "
            + shlex.quote(job_id)
            + " --format=State,ExitCode | head -n 1"
        )
        result = self.run_ssh(sacct)
        if result.returncode == 0 and result.stdout.strip():
            return state_base(result.stdout)
        return "UNKNOWN"

    def read_logs(self, stdout_path: str | None, stderr_path: str | None) -> str:
        parts = []
        for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            if not path:
                continue
            result = self.run_ssh("cat " + shlex.quote(path))
            parts.append(f"--- {label}: {path} ---\n{result.stdout}\n{result.stderr}")
        return "\n".join(parts)

    def cache_status(self, cache_dir: str, expected: int) -> dict[str, Any]:
        remote_script = """
import json
import sys
from pathlib import Path

cache = Path(sys.argv[1])
expected = int(sys.argv[2])
sifs = list(cache.glob("*.sif")) if cache.is_dir() else []
failed = sorted(str(path) for path in cache.glob("preheat_failed_images_*.txt")) if cache.is_dir() else []
print(json.dumps({
    "sif_count": len(sifs),
    "expected": expected,
    "complete": len(sifs) >= expected,
    "failed_lists": failed[-5:],
}, sort_keys=True))
"""
        command = (
            "python3 -c "
            + shlex.quote(remote_script)
            + " "
            + shlex.quote(cache_dir)
            + " "
            + shlex.quote(str(expected))
        )
        result = self.run_ssh(command)
        if result.returncode != 0:
            return {"complete": False, "sif_count": 0, "error": result.stderr.strip()}
        return json.loads(result.stdout.strip().splitlines()[-1])
