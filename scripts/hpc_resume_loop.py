#!/usr/bin/env python3
"""Submit resumable GEPA HPC jobs in short Slurm slices."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
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
OFFLINE_TARGET_EXTENSION_SCRIPT = (
    REPO_ROOT / "scripts" / "internal" / "offline_iteration_target.py"
)
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
    "controller_failed",
    "invalid_controller_status",
    "invalid_progress",
    "invalid_result",
    "state_without_manifest",
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
    target_iterations: int
    once: bool
    state_file: Path
    runtime_config_sha256: str
    require_clean_worktree: bool
    repo_commit: str | None
    offline_gepa: bool


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_output(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


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
    parser.add_argument("--slice-time", default="02:00:00")
    parser.add_argument(
        "--check-interval",
        default="0",
        help="Deprecated compatibility option; supervisor cadence uses --poll-interval.",
    )
    parser.add_argument("--poll-interval", default="1800")
    parser.add_argument(
        "--target-iterations",
        type=int,
        default=0,
        help=(
            "Stop at this cumulative durable GEPA iteration count; "
            "0 uses run completion."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform one remote decision without sleeping.",
    )
    parser.add_argument(
        "--state-file",
        help="Local supervisor state path (default: .local/hpc-supervisor/<job>.json).",
    )
    parser.add_argument(
        "--require-clean-worktree",
        action="store_true",
        help=(
            "Block controller submission if the tracked Git commit changes or "
            "the worktree becomes dirty."
        ),
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Maximum submitted slices; 0 means unlimited.",
    )
    parser.add_argument(
        "--batch-script",
        default=os.environ.get("VIBE_HPC_SUBMIT_BATCH", str(DEFAULT_BATCH_SCRIPT)),
    )
    known, batch_args = parser.parse_known_args(argv)
    if known.max_runs < 0:
        raise SystemExit("--max-runs must be non-negative")
    if known.target_iterations < 0:
        raise SystemExit("--target-iterations must be non-negative")
    gepa_config_raw = _take_option(batch_args, "--gepa-config")
    if not gepa_config_raw:
        raise SystemExit("--gepa-config is required")
    gepa_config = _resolve_repo_path(gepa_config_raw)
    offline_gepa = "--gepa-rules" in batch_args
    target_iterations = known.target_iterations
    if offline_gepa and target_iterations == 0:
        configured_target = (
            _load_yaml(gepa_config).get("search", {}).get("max_iterations")
        )
        if configured_target is not None:
            target_iterations = int(configured_target)
    if target_iterations < 0:
        raise SystemExit("cumulative iteration target must be non-negative")
    submit = "--submit" in batch_args
    job_name = _take_option(batch_args, "--job-name") or "vibe-gepa"
    state_file = Path(
        known.state_file
        or REPO_ROOT
        / ".local"
        / "hpc-supervisor"
        / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', job_name)}.json"
    )
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
        target_iterations=target_iterations,
        once=known.once,
        state_file=state_file,
        runtime_config_sha256=_sha256(gepa_config),
        require_clean_worktree=known.require_clean_worktree,
        repo_commit=_git_output("rev-parse", "HEAD"),
        offline_gepa=offline_gepa,
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


def _submission_identity_error(config: SupervisorConfig) -> str | None:
    current_config_sha256 = _sha256(config.gepa_config)
    if current_config_sha256 != config.runtime_config_sha256:
        return (
            "runtime GEPA config changed after supervisor start: "
            f"{config.gepa_config}"
        )
    if not config.require_clean_worktree:
        return None
    current_commit = _git_output("rev-parse", "HEAD")
    if current_commit is None or current_commit != config.repo_commit:
        return (
            "tracked Git commit changed after supervisor start: "
            f"expected={config.repo_commit} actual={current_commit}"
        )
    worktree = _git_output("status", "--porcelain=v1", "--untracked-files=all")
    if worktree is None:
        return "could not inspect Git worktree before controller submission"
    if worktree:
        return "Git worktree is not clean before controller submission"
    return None


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
import subprocess
import sys
from pathlib import Path

run_dir = Path(os.path.expanduser(sys.argv[1]))
controller_job_name = sys.argv[2]
result_path = run_dir / "result.json"
state_path = run_dir / "gepa_state.bin"
manifest_path = run_dir / "run_manifest.json"
online_manifest_path = run_dir / "online_run_manifest.json"
progress_path = run_dir / "iteration_progress.json"
if not progress_path.is_file():
    progress_path = run_dir / "online_iteration_progress.json"
controller_status_path = run_dir / "controller_status.json"
payload = {"state": "missing", "run_dir": str(run_dir)}
if result_path.is_file():
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        payload = {"state": "invalid_result", "error": str(exc), "run_dir": str(run_dir)}
    else:
        status = result.get("run_status") or result.get("status")
        if status is None and online_manifest_path.is_file():
            status = "completed"
        payload = {"state": "result", "status": status, "run_dir": str(run_dir)}
elif state_path.is_file() and (manifest_path.is_file() or online_manifest_path.is_file()):
    payload = {"state": "resumable", "run_dir": str(run_dir)}
elif state_path.is_file():
    payload = {"state": "state_without_manifest", "run_dir": str(run_dir)}

if controller_status_path.is_file():
    try:
        controller_status = json.loads(controller_status_path.read_text(encoding="utf-8"))
        payload["controller_status"] = controller_status.get("status")
        payload["controller_failure_phase"] = controller_status.get("failure_phase")
        if controller_status.get("status") == "failed":
            payload["state"] = "controller_failed"
            payload["error_type"] = controller_status.get("error_type")
        elif controller_status.get("status") == "retryable_failed":
            payload["state"] = "resumable"
            payload["retryable_controller_error_type"] = controller_status.get("error_type")
    except Exception as exc:
        payload = {"state": "invalid_controller_status", "error": str(exc), "run_dir": str(run_dir)}

if progress_path.is_file():
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        payload["completed_iterations"] = int(progress["completed_iterations"])
        payload["first_observed_completed_iterations"] = int(
            progress["first_observed_completed_iterations"]
        )
    except Exception as exc:
        payload = {"state": "invalid_progress", "error": str(exc), "run_dir": str(run_dir)}

def slurm_state(job_id):
    queued = subprocess.run(
        ["squeue", "-h", "-j", str(job_id), "-o", "%T"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if queued.returncode == 0 and queued.stdout.strip():
        return queued.stdout.strip().splitlines()[0].split()[0].upper()
    accounted = subprocess.run(
        ["sacct", "-n", "-P", "-j", str(job_id), "--format=State"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if accounted.returncode == 0 and accounted.stdout.strip():
        return accounted.stdout.strip().splitlines()[0].split("|", 1)[0].split()[0].upper()
    return "UNKNOWN"

controllers = subprocess.run(
    ["squeue", "-h", "-n", controller_job_name, "-o", "%A|%T"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
)
payload["active_controllers"] = []
if controllers.returncode == 0:
    payload["active_controllers"] = [
        line for line in controllers.stdout.splitlines()
        if line.strip() and line.rsplit("|", 1)[-1].strip().upper() not in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}
    ]

worker_ids = set()
for path in (run_dir / "hpc_rollout_batches").glob("batch_*/batch_state.json"):
    try:
        batch_state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if batch_state.get("phase") == "COMPLETE":
        continue
    if batch_state.get("active_job_id"):
        worker_ids.add(str(batch_state["active_job_id"]))
for path in (run_dir / "hpc_tasks").glob("**/task_state.json"):
    try:
        task_state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if task_state.get("phase") == "COMPLETE":
        continue
    if task_state.get("active_job_id"):
        worker_ids.add(str(task_state["active_job_id"]))
payload["worker_states"] = {job_id: slurm_state(job_id) for job_id in sorted(worker_ids)}
payload["active_workers"] = [
    job_id for job_id, state in payload["worker_states"].items()
    if state not in {"BOOT_FAIL", "CANCELLED", "COMPLETED", "DEADLINE", "FAILED", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "REVOKED", "SPECIAL_EXIT", "TIMEOUT"}
]
print(json.dumps(payload, sort_keys=True))
"""
    remote_command = (
        "printf VIBE_HPC_RUN_STATUS >/dev/null; python3 -c "
        + shlex.quote(script)
        + " "
        + shlex.quote(config.remote_run_snapshot)
        + " "
        + shlex.quote(config.job_name)
    )
    result = run_command(_ssh_command(config, remote_command))
    if result.returncode != 0:
        return {
            "state": "status_check_failed",
            "returncode": result.returncode,
            "stderr": result.stderr.strip(),
        }
    return json.loads(result.stdout.strip().splitlines()[-1])


def extend_completed_offline_target(
    config: SupervisorConfig,
    status: dict[str, Any],
) -> dict[str, Any]:
    """Increase a completed Offline run target before ordinary resume."""

    if not config.offline_gepa:
        raise RuntimeError("completed-target extension is Offline-only")
    completed = status.get("completed_iterations")
    if completed is None:
        raise RuntimeError("completed Offline run has no durable iteration count")
    source = OFFLINE_TARGET_EXTENSION_SCRIPT.read_text(encoding="utf-8")
    invocation = """
import json
import sys
print(json.dumps(extend_iteration_target(
    Path(sys.argv[1]),
    int(sys.argv[2]),
    "supervisor cumulative-target resume",
), sort_keys=True))
"""
    remote_command = (
        "printf VIBE_OFFLINE_TARGET_EXTENSION >/dev/null; python3 -c "
        + shlex.quote(source + invocation)
        + " "
        + shlex.quote(config.remote_run_snapshot)
        + " "
        + shlex.quote(str(config.target_iterations))
    )
    result = run_command(_ssh_command(config, remote_command))
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            "could not extend completed Offline iteration target: " + error
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def is_completed(status: dict[str, Any]) -> bool:
    raw_run_status = status.get("status")
    raw_controller_status = status.get("controller_status")
    run_status = str(raw_run_status).lower() if raw_run_status is not None else ""
    controller_status = (
        str(raw_controller_status).lower()
        if raw_controller_status is not None
        else ""
    )
    return (
        status.get("state") == "result"
        and (
            run_status in SUCCESS_RUN_STATUSES
            or (
                not run_status
                and controller_status in SUCCESS_RUN_STATUSES
            )
        )
    )


def is_failed(status: dict[str, Any]) -> bool:
    state = status.get("state")
    run_status = str(status.get("status", "")).lower()
    return state in BLOCKING_STATUS_STATES or (
        state == "result" and run_status in FAILED_RUN_STATUSES
    )


def _load_supervisor_state(config: SupervisorConfig) -> dict[str, Any]:
    if not config.state_file.is_file():
        return {
            "schema_version": 2,
            "submissions": 0,
            "remote_run_snapshot": config.remote_run_snapshot,
            "job_name": config.job_name,
            "target_iterations": config.target_iterations,
            "runtime_config_sha256": config.runtime_config_sha256,
            "repo_commit": config.repo_commit,
        }
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    if state.get("schema_version") == 1:
        previous_cumulative_target = state.get("target_completed_iterations")
        if previous_cumulative_target is None:
            raise RuntimeError(
                "legacy supervisor state has no cumulative target; use a new "
                "--state-file"
            )
        state["schema_version"] = 2
        state["target_iterations"] = int(previous_cumulative_target)
    elif state.get("schema_version") != 2:
        raise RuntimeError("unsupported supervisor state schema")
    expected = {
        "remote_run_snapshot": config.remote_run_snapshot,
        "job_name": config.job_name,
        "target_iterations": config.target_iterations,
        "runtime_config_sha256": config.runtime_config_sha256,
        "repo_commit": config.repo_commit,
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise RuntimeError(
                f"supervisor state differs at {key}; use the original options "
                "or a new --state-file"
            )
    return state


def _save_supervisor_state(config: SupervisorConfig, state: dict[str, Any]) -> None:
    config.state_file.parent.mkdir(parents=True, exist_ok=True)
    temp = config.state_file.with_suffix(config.state_file.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(config.state_file)


@contextmanager
def _supervisor_lock(config: SupervisorConfig):
    lock_path = config.state_file.with_suffix(config.state_file.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"another supervisor holds {lock_path}") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _iteration_target_reached(
    config: SupervisorConfig,
    status: dict[str, Any],
    state: dict[str, Any],
) -> bool:
    if config.target_iterations == 0:
        return False
    completed = status.get("completed_iterations")
    if completed is None:
        return False
    state["last_completed_iterations"] = int(completed)
    return int(completed) >= config.target_iterations


def run_loop(config: SupervisorConfig) -> int:
    if not config.submit:
        return run_dry_run(config)

    with _supervisor_lock(config):
        state = _load_supervisor_state(config)
        print(
            "[hpc-resume] starting supervisor "
            f"slice_time={config.slice_time} poll_interval={config.poll_interval_seconds}s "
            f"cumulative_target_iterations={config.target_iterations} "
            f"max_runs={config.max_runs} "
            f"remote_run_snapshot={config.remote_run_snapshot}"
        )
        while True:
            identity_error = _submission_identity_error(config)
            if identity_error is not None:
                state["status"] = "blocked_identity_mismatch"
                state["identity_error"] = identity_error
                _save_supervisor_state(config, state)
                print(
                    f"[hpc-resume] identity check failed: {identity_error}; "
                    "not submitting",
                    file=sys.stderr,
                )
                return 1
            status = remote_run_status(config)
            state["last_remote_status"] = status
            if status.get("completed_iterations") is not None:
                state["last_completed_iterations"] = int(
                    status["completed_iterations"]
                )
            print(f"[hpc-resume] status={json.dumps(status, sort_keys=True)}")
            if status.get("state") == "status_check_failed":
                state["status"] = "waiting_after_status_check_failure"
                _save_supervisor_state(config, state)
                print(
                    "[hpc-resume] remote status check failed; retaining supervisor "
                    "and retrying without submission",
                    file=sys.stderr,
                )
                if config.once:
                    return 0
                if config.poll_interval_seconds > 0:
                    time.sleep(config.poll_interval_seconds)
                continue
            if is_failed(status):
                state["status"] = "blocked"
                _save_supervisor_state(config, state)
                print("[hpc-resume] run is blocked; not resubmitting", file=sys.stderr)
                return 1

            active_controllers = status.get("active_controllers", [])
            active_workers = status.get("active_workers", [])
            if active_controllers or active_workers:
                state["status"] = (
                    "waiting_controller" if active_controllers else "waiting_workers"
                )
                print(
                    "[hpc-resume] waiting without submission "
                    f"controllers={active_controllers} workers={active_workers}"
                )
                _save_supervisor_state(config, state)
                if config.once:
                    return 0
                if config.poll_interval_seconds > 0:
                    time.sleep(config.poll_interval_seconds)
                continue

            if is_completed(status):
                if not config.target_iterations or _iteration_target_reached(
                    config, status, state
                ):
                    state["status"] = "completed"
                    _save_supervisor_state(config, state)
                    return 0
                if not config.offline_gepa:
                    state["status"] = "completed_before_iteration_target"
                    _save_supervisor_state(config, state)
                    print(
                        "[hpc-resume] run completed before cumulative iteration target",
                        file=sys.stderr,
                    )
                    return 2
                try:
                    extension = extend_completed_offline_target(config, status)
                except Exception as exc:
                    state["status"] = "blocked_iteration_target_extension"
                    state["iteration_target_extension_error"] = {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    _save_supervisor_state(config, state)
                    print(
                        "[hpc-resume] completed Offline target could not be extended: "
                        f"{exc}",
                        file=sys.stderr,
                    )
                    return 1
                state["last_iteration_target_extension"] = extension
                state["status"] = "iteration_target_extended"
                _save_supervisor_state(config, state)
                print(
                    "[hpc-resume] extended completed Offline target "
                    f"from={extension['from']} to={extension['to']}"
                )
                continue
            if _iteration_target_reached(config, status, state):
                state["status"] = "iteration_target_reached"
                _save_supervisor_state(config, state)
                print("[hpc-resume] iteration target reached")
                return 0

            submissions = int(state.get("submissions", 0))
            if config.max_runs != 0 and submissions >= config.max_runs:
                state["status"] = "max_runs_reached"
                _save_supervisor_state(config, state)
                print("[hpc-resume] max runs reached", file=sys.stderr)
                return 2
            identity_error = _submission_identity_error(config)
            if identity_error is not None:
                state["status"] = "blocked_identity_mismatch"
                state["identity_error"] = identity_error
                _save_supervisor_state(config, state)
                print(
                    f"[hpc-resume] identity check failed: {identity_error}; "
                    "not submitting",
                    file=sys.stderr,
                )
                return 1
            try:
                job_id = submit_slice(config)
            except Exception as exc:
                state["status"] = "waiting_after_submission_failure"
                state["last_submission_error"] = {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                _save_supervisor_state(config, state)
                print(
                    "[hpc-resume] controller submission failed; retaining "
                    "supervisor and retrying later",
                    file=sys.stderr,
                )
                if config.once:
                    return 0
                if config.poll_interval_seconds > 0:
                    time.sleep(config.poll_interval_seconds)
                continue
            state["submissions"] = submissions + 1
            state["last_controller_job_id"] = job_id
            state["status"] = "controller_submitted"

            _save_supervisor_state(config, state)
            if config.once:
                return 0
            if config.poll_interval_seconds > 0:
                time.sleep(config.poll_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    config = parse_args(sys.argv[1:] if argv is None else argv)
    return run_loop(config)


if __name__ == "__main__":
    raise SystemExit(main())
