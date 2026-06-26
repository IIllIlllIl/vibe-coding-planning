#!/usr/bin/env python3
"""Submit resumable GEPA HPC jobs in short Slurm slices."""

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


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH_SCRIPT = REPO_ROOT / "scripts" / "hpc_submit_batch.sh"
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
SUCCESS_RUN_STATUSES = {"completed", "completed_with_warnings"}
FAILED_RUN_STATUSES = {"failed"}
BLOCKING_STATUS_STATES = {
    "invalid_result",
    "state_without_manifest",
    "status_check_failed",
}


@dataclass(frozen=True)
class SupervisorConfig:
    batch_args: list[str]
    gepa_config: Path
    slice_time: str
    check_interval_seconds: int
    poll_interval_seconds: int
    max_runs: int
    submit: bool
    remote_run_snapshot: str
    job_name: str
    ssh_target: str
    ssh_port: str
    ssh_key: str
    batch_script: Path


def parse_duration(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    if re.fullmatch(r"\d+h", raw):
        return int(raw[:-1]) * 3600
    if re.fullmatch(r"\d+m", raw):
        return int(raw[:-1]) * 60
    parts = raw.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        hours = 0
        minutes, seconds = (int(part) for part in parts)
        return hours * 3600 + minutes * 60 + seconds
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        hours, minutes, seconds = (int(part) for part in parts)
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"invalid duration: {raw}")


def _take_option(args: list[str], option: str) -> str | None:
    for index, value in enumerate(args):
        if value == option:
            if index + 1 >= len(args):
                raise SystemExit(f"{option} requires a value")
            return args[index + 1]
        prefix = f"{option}="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def _remove_options(args: list[str], options: set[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value in options:
            index += 2
            continue
        if any(value.startswith(f"{option}=") for option in options):
            index += 1
            continue
        result.append(value)
        index += 1
    return result


def _remove_flags(args: list[str], flags: set[str]) -> list[str]:
    return [arg for arg in args if arg not in flags]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolve_repo_path(raw: str, base: Path = REPO_ROOT) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else base / path


def _repo_relative(path: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"{label} must be inside the repository: {path}") from exc


def _remote_run_snapshot(batch_args: list[str], gepa_config: Path) -> str:
    gepa = _load_yaml(gepa_config)
    paths = gepa.get("paths", {})
    run_dir_raw = paths.get("run_dir")
    if not run_dir_raw:
        raise SystemExit(f"GEPA config has no paths.run_dir: {gepa_config}")
    run_dir = _resolve_repo_path(str(run_dir_raw), gepa_config.parent.parent)
    run_rel = _repo_relative(run_dir, "run_dir")
    remote_run_dir = (
        _take_option(batch_args, "--remote-run-dir")
        or "~/hpc_run_state/vibe-coding-planning"
    )
    return f"{remote_run_dir.rstrip('/')}/{run_rel}"


def _ssh_config(batch_args: list[str]) -> tuple[str, str, str]:
    ulhpc_config_raw = _take_option(batch_args, "--ulhpc-config")
    ulhpc_config = (
        Path(ulhpc_config_raw)
        if ulhpc_config_raw
        else REPO_ROOT / "configs" / "ulhpc_submit.yaml"
    )
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


def parse_args(argv: list[str]) -> SupervisorConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Repeatedly submit short resumable GEPA jobs through "
            "scripts/hpc_submit_batch.sh."
        ),
        allow_abbrev=False,
    )
    parser.add_argument("--slice-time", default="12:00:00")
    parser.add_argument("--check-interval", default="01:00:00")
    parser.add_argument("--poll-interval", default="1800")
    parser.add_argument(
        "--max-runs",
        type=int,
        default=4,
        help="Maximum submitted slices; 0 means unlimited.",
    )
    parser.add_argument(
        "--batch-script",
        default=os.environ.get("VIBE_HPC_SUBMIT_BATCH", str(DEFAULT_BATCH_SCRIPT)),
    )
    known, batch_args = parser.parse_known_args(argv)
    if known.max_runs < 0:
        raise SystemExit("--max-runs must be non-negative")
    gepa_config_raw = _take_option(batch_args, "--gepa-config")
    if not gepa_config_raw:
        raise SystemExit("--gepa-config is required")
    gepa_config = _resolve_repo_path(gepa_config_raw)
    submit = "--submit" in batch_args
    job_name = _take_option(batch_args, "--job-name") or "vibe-gepa"
    ssh_target, ssh_port, ssh_key = _ssh_config(batch_args)
    return SupervisorConfig(
        batch_args=batch_args,
        gepa_config=gepa_config,
        slice_time=known.slice_time,
        check_interval_seconds=parse_duration(known.check_interval),
        poll_interval_seconds=parse_duration(known.poll_interval),
        max_runs=known.max_runs,
        submit=submit,
        remote_run_snapshot=_remote_run_snapshot(batch_args, gepa_config),
        job_name=job_name,
        ssh_target=ssh_target,
        ssh_port=ssh_port,
        ssh_key=ssh_key,
        batch_script=Path(known.batch_script),
    )


def _ssh_command(config: SupervisorConfig, remote_command: str) -> list[str]:
    command = ["ssh", "-p", config.ssh_port]
    if config.ssh_key:
        command.extend(["-i", os.path.expanduser(config.ssh_key)])
    command.extend([config.ssh_target, remote_command])
    return command


def run_command(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


def _batch_command(config: SupervisorConfig, *, submit: bool) -> list[str]:
    args = _remove_options(config.batch_args, {"--time"})
    args = _remove_flags(args, {"--submit", "--dry-run"})
    args.extend(["--time", config.slice_time])
    args.append("--submit" if submit else "--dry-run")
    return ["bash", str(config.batch_script), *args]


def _extract_job_id(stdout: str) -> str:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("job_id"):
            return str(payload["job_id"])
    raise RuntimeError("could not find job_id in hpc_submit_batch output")


def submit_slice(config: SupervisorConfig) -> str:
    command = _batch_command(config, submit=True)
    print(
        f"[hpc-resume] submitting slice: {' '.join(shlex.quote(x) for x in command)}",
        flush=True,
    )
    result = run_command(command)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"hpc_submit_batch failed with rc={result.returncode}")
    job_id = _extract_job_id(result.stdout)
    print(f"[hpc-resume] submitted job_id={job_id}")
    return job_id


def run_dry_run(config: SupervisorConfig) -> int:
    command = _batch_command(config, submit=False)
    print(
        f"[hpc-resume] dry-run: {' '.join(shlex.quote(x) for x in command)}",
        flush=True,
    )
    result = run_command(command)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def _state_base(raw: str) -> str:
    return raw.strip().split("|", 1)[0].split()[0].split("+", 1)[0]


def get_job_state(config: SupervisorConfig, job_id: str) -> str:
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


def wait_for_job(config: SupervisorConfig, job_id: str) -> str:
    while True:
        state = get_job_state(config, job_id)
        print(f"[hpc-resume] job_id={job_id} state={state}")
        if state in TERMINAL_JOB_STATES:
            return state
        if state == "UNKNOWN":
            return state
        if config.poll_interval_seconds > 0:
            time.sleep(config.poll_interval_seconds)


def remote_run_status(config: SupervisorConfig) -> dict[str, Any]:
    script = r"""
import json
import os
import sys
from pathlib import Path

run_dir = Path(os.path.expanduser(sys.argv[1]))
result_path = run_dir / "result.json"
state_path = run_dir / "gepa_state.bin"
manifest_path = run_dir / "run_manifest.json"
payload = {"state": "missing", "run_dir": str(run_dir)}
if result_path.is_file():
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        payload = {"state": "invalid_result", "error": str(exc), "run_dir": str(run_dir)}
    else:
        status = result.get("run_status") or result.get("status")
        payload = {"state": "result", "status": status, "run_dir": str(run_dir)}
elif state_path.is_file() and manifest_path.is_file():
    payload = {"state": "resumable", "run_dir": str(run_dir)}
elif state_path.is_file():
    payload = {"state": "state_without_manifest", "run_dir": str(run_dir)}
print(json.dumps(payload, sort_keys=True))
"""
    remote_command = (
        "printf VIBE_HPC_RUN_STATUS >/dev/null; python3 -c "
        + shlex.quote(script)
        + " "
        + shlex.quote(config.remote_run_snapshot)
    )
    result = run_command(_ssh_command(config, remote_command))
    if result.returncode != 0:
        return {
            "state": "status_check_failed",
            "returncode": result.returncode,
            "stderr": result.stderr.strip(),
        }
    return json.loads(result.stdout.strip().splitlines()[-1])


def is_completed(status: dict[str, Any]) -> bool:
    return (
        status.get("state") == "result"
        and str(status.get("status", "")).lower() in SUCCESS_RUN_STATUSES
    )


def is_failed(status: dict[str, Any]) -> bool:
    state = status.get("state")
    run_status = str(status.get("status", "")).lower()
    return state in BLOCKING_STATUS_STATES or (
        state == "result" and run_status in FAILED_RUN_STATUSES
    )


def run_loop(config: SupervisorConfig) -> int:
    if not config.submit:
        return run_dry_run(config)

    print(
        "[hpc-resume] starting loop "
        f"slice_time={config.slice_time} max_runs={config.max_runs} "
        f"check_interval={config.check_interval_seconds}s "
        f"remote_run_snapshot={config.remote_run_snapshot}"
    )
    runs = 0
    while config.max_runs == 0 or runs < config.max_runs:
        status = remote_run_status(config)
        print(f"[hpc-resume] pre-submit status={json.dumps(status, sort_keys=True)}")
        if is_completed(status):
            print("[hpc-resume] run already completed")
            return 0
        if is_failed(status):
            print("[hpc-resume] run is failed; not resubmitting", file=sys.stderr)
            return 1

        runs += 1
        job_id = submit_slice(config)
        job_state = wait_for_job(config, job_id)
        status = remote_run_status(config)
        print(
            "[hpc-resume] post-job "
            f"job_id={job_id} job_state={job_state} "
            f"status={json.dumps(status, sort_keys=True)}"
        )
        if is_completed(status):
            print("[hpc-resume] completed")
            return 0
        if is_failed(status):
            print("[hpc-resume] run failed; stopping", file=sys.stderr)
            return 1
        if config.max_runs != 0 and runs >= config.max_runs:
            break
        if config.check_interval_seconds > 0:
            print(f"[hpc-resume] sleeping {config.check_interval_seconds}s before resume")
            time.sleep(config.check_interval_seconds)

    print("[hpc-resume] max runs reached before completion", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    config = parse_args(sys.argv[1:] if argv is None else argv)
    return run_loop(config)


if __name__ == "__main__":
    raise SystemExit(main())
