#!/usr/bin/env python3
"""Submit SIF preheat jobs in resumable Slurm slices."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREHEAT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "submit_apptainer_sif_preheat.sh"
TERMINAL_JOB_STATES = {
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
class Config:
    gepa_config: Path
    preheat_script: Path
    ulhpc_config: Path
    remote_project_dir: str
    remote_dataset_dir: str
    sif_cache_dir: str
    job_name: str
    slice_time: str
    check_interval_seconds: int
    poll_interval_seconds: int
    max_runs: int
    cpus: str
    mem: str
    timeout: str
    max_attempts: str
    retry_backoff: str
    ssh_target: str
    ssh_port: str
    ssh_key: str
    submit: bool


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
    raise ValueError(f"invalid duration: {raw}")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _ssh_config(ulhpc_config: Path) -> tuple[str, str, str]:
    data = _load_yaml(ulhpc_config)
    host = os.environ.get("ULHPC_HOST") or str(data.get("host", "access-iris.uni.lu"))
    port = os.environ.get("ULHPC_PORT") or str(data.get("port", "8022"))
    user = os.environ.get("ULHPC_USER") or str(data.get("user", ""))
    ssh_key = os.environ.get("ULHPC_SSH_KEY") or str(data.get("ssh_key", ""))
    if not user:
        raise SystemExit(
            "cannot determine ULHPC user; set configs/ulhpc_submit.yaml user or ULHPC_USER"
        )
    return f"{user}@{host}", port, ssh_key


def _default_hpc_root() -> str:
    user = os.environ.get("ULHPC_USER") or os.environ.get("USER") or "<user>"
    return os.environ.get(
        "VIBE_HPC_ROOT",
        f"/scratch/users/{user}/vibe-coding-planning",
    )


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        description="Repeatedly submit short SIF preheat jobs to ULHPC.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True, help="GEPA config path")
    parser.add_argument(
        "--preheat-script",
        default=str(DEFAULT_PREHEAT_SCRIPT),
        help="Local submit_apptainer_sif_preheat.sh wrapper path",
    )
    parser.add_argument("--ulhpc-config", default="configs/ulhpc_submit.yaml")
    parser.add_argument(
        "--remote-project-dir",
        default=f"{_default_hpc_root()}/runs/vibe-sif-preheat",
    )
    parser.add_argument(
        "--remote-dataset-dir",
        default=f"{_default_hpc_root()}/hpc_datasets",
    )
    parser.add_argument(
        "--sif-cache-dir",
        default=f"{_default_hpc_root()}/shared/sif-cache",
    )
    parser.add_argument("--job-name", default="gepa-preheat-sifs-8h")
    parser.add_argument("--slice-time", default="08:00:00")
    parser.add_argument("--check-interval", default="01:00:00")
    parser.add_argument("--poll-interval", default="1800")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--cpus", default="1")
    parser.add_argument("--mem", default="4G")
    parser.add_argument("--timeout", default="0")
    parser.add_argument("--max-attempts", default="1")
    parser.add_argument("--retry-backoff", default="0")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args(argv)
    if args.max_runs < 0:
        raise SystemExit("--max-runs must be non-negative")
    ssh_target, ssh_port, ssh_key = _ssh_config(REPO_ROOT / args.ulhpc_config)
    return Config(
        gepa_config=(REPO_ROOT / args.config).resolve(),
        preheat_script=(REPO_ROOT / args.preheat_script).resolve(),
        ulhpc_config=(REPO_ROOT / args.ulhpc_config).resolve(),
        remote_project_dir=args.remote_project_dir,
        remote_dataset_dir=args.remote_dataset_dir,
        sif_cache_dir=args.sif_cache_dir,
        job_name=args.job_name,
        slice_time=args.slice_time,
        check_interval_seconds=parse_duration(args.check_interval),
        poll_interval_seconds=parse_duration(args.poll_interval),
        max_runs=args.max_runs,
        cpus=args.cpus,
        mem=args.mem,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        retry_backoff=args.retry_backoff,
        ssh_target=ssh_target,
        ssh_port=ssh_port,
        ssh_key=ssh_key,
        submit=args.submit,
    )


def _ssh_command(config: Config, remote_command: str) -> list[str]:
    command = ["ssh", "-p", config.ssh_port]
    if config.ssh_key:
        command.extend(["-i", os.path.expanduser(config.ssh_key)])
    command.extend([config.ssh_target, remote_command])
    return command


def run_command(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def expected_image_count(config_path: Path) -> int:
    from scripts.tools.prepare_apptainer_sifs import _collect_images
    from src.optimization.config import load_optimization_config

    return len(_collect_images(load_optimization_config(config_path)))


def remote_cache_status(config: Config, expected: int) -> dict[str, Any]:
    remote_script = """
import json
import sys
from pathlib import Path

cache = Path(sys.argv[1])
expected = int(sys.argv[2])
sifs = list(cache.glob("*.sif")) if cache.is_dir() else []
failed = sorted(str(path) for path in cache.glob("preheat_failed_images_*.txt")) if cache.is_dir() else []
payload = {
    "sif_count": len(sifs),
    "expected": expected,
    "complete": len(sifs) >= expected,
    "failed_lists": failed[-5:],
}
print(json.dumps(payload, sort_keys=True))
"""
    command = (
        "python3 -c "
        + shlex.quote(remote_script)
        + " "
        + shlex.quote(config.sif_cache_dir)
        + " "
        + shlex.quote(str(expected))
    )
    result = run_command(_ssh_command(config, command))
    if result.returncode != 0:
        return {
            "complete": False,
            "error": result.stderr.strip(),
            "returncode": result.returncode,
        }
    return json.loads(result.stdout.strip().splitlines()[-1])


def _relative_to_repo(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _extract_job_id(stdout: str) -> str | None:
    stripped = stdout.strip()
    if stripped:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("job_id"):
            return str(payload["job_id"])

    json_match = re.search(r'"job_id"\s*:\s*"?(?P<job_id>[A-Za-z0-9_.-]+)"?', stdout)
    if json_match:
        return json_match.group("job_id")

    text_match = re.search(r"submitted job\s+(?P<job_id>[A-Za-z0-9_.-]+)", stdout, re.I)
    if text_match:
        return text_match.group("job_id")
    return None


def submit_slice(config: Config) -> str:
    command = [
        "bash",
        str(config.preheat_script),
        "--config",
        _relative_to_repo(config.gepa_config),
        "--sif-cache-dir",
        config.sif_cache_dir,
        "--job-name",
        config.job_name,
        "--time",
        config.slice_time,
        "--cpus",
        config.cpus,
        "--mem",
        config.mem,
        "--timeout",
        config.timeout,
        "--max-attempts",
        config.max_attempts,
        "--retry-backoff",
        config.retry_backoff,
        "--remote-dir",
        config.remote_project_dir,
        "--remote-dataset-dir",
        config.remote_dataset_dir,
        "--ulhpc-config",
        str(config.ulhpc_config),
        "--submit",
    ]
    result = run_command(command)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"preheat submit failed with rc={result.returncode}")
    job_id = _extract_job_id(result.stdout)
    if not job_id:
        raise RuntimeError("could not find submitted preheat job id")
    return job_id


def _state_base(raw: str) -> str:
    return raw.strip().split("|", 1)[0].split()[0].split("+", 1)[0]


def get_job_state(config: Config, job_id: str) -> str:
    squeue = "squeue -h -j " + shlex.quote(job_id) + " -o %T | head -n 1"
    result = run_command(_ssh_command(config, squeue))
    if result.returncode == 0 and result.stdout.strip():
        return _state_base(result.stdout)

    sacct = (
        "sacct -n -P -j "
        + shlex.quote(job_id)
        + " --format=State,ExitCode | head -n 1"
    )
    result = run_command(_ssh_command(config, sacct))
    if result.returncode == 0 and result.stdout.strip():
        return _state_base(result.stdout)
    return "UNKNOWN"


def wait_for_job(config: Config, job_id: str) -> str:
    while True:
        state = get_job_state(config, job_id)
        print(f"[sif-preheat] job_id={job_id} state={state}", flush=True)
        if state in TERMINAL_JOB_STATES or state == "UNKNOWN":
            return state
        if config.poll_interval_seconds > 0:
            time.sleep(config.poll_interval_seconds)


def run_loop(config: Config) -> int:
    expected = expected_image_count(config.gepa_config)
    print(
        "[sif-preheat] starting loop "
        f"expected={expected} slice_time={config.slice_time} "
        f"max_runs={config.max_runs} poll_interval={config.poll_interval_seconds}s",
        flush=True,
    )
    if not config.submit:
        status = remote_cache_status(config, expected)
        print(f"[sif-preheat] dry-run status={json.dumps(status, sort_keys=True)}")
        return 0

    runs = 0
    while config.max_runs == 0 or runs < config.max_runs:
        status = remote_cache_status(config, expected)
        print(f"[sif-preheat] pre-submit status={json.dumps(status, sort_keys=True)}")
        if status.get("complete"):
            print("[sif-preheat] cache already complete")
            return 0

        runs += 1
        job_id = submit_slice(config)
        print(f"[sif-preheat] submitted job_id={job_id}", flush=True)
        job_state = wait_for_job(config, job_id)
        status = remote_cache_status(config, expected)
        print(
            "[sif-preheat] post-job "
            f"job_id={job_id} job_state={job_state} "
            f"status={json.dumps(status, sort_keys=True)}"
        )
        if status.get("complete"):
            print("[sif-preheat] completed")
            return 0
        if config.max_runs != 0 and runs >= config.max_runs:
            break
        if config.check_interval_seconds > 0:
            print(f"[sif-preheat] sleeping {config.check_interval_seconds}s before next slice")
            time.sleep(config.check_interval_seconds)

    print("[sif-preheat] max runs reached before completion", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    config = parse_args(sys.argv[1:] if argv is None else argv)
    return run_loop(config)


if __name__ == "__main__":
    raise SystemExit(main())
