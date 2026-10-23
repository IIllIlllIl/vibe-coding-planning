"""Small, method-independent Slurm command boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Any


@dataclass(frozen=True)
class SlurmTaskStatus:
    state: str
    elapsed_seconds: int | None = None
    raw: str = ""


class SlurmSubmissionUnknown(RuntimeError):
    """The scheduler may have accepted the job, but its receipt was lost."""


class SlurmSubmissionRejected(RuntimeError):
    """The scheduler explicitly rejected the job specification or permission."""


class SlurmLookupUnavailable(RuntimeError):
    """Absence of a matching job could not be established from scheduler state."""


def _run_optional_command(args: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except Exception as exc:
        return {
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }
    return {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def normalize_slurm_state(value: str) -> str:
    return value.strip().split()[0].upper() if value.strip() else "UNKNOWN"


def parse_slurm_duration(value: str) -> int | None:
    value = value.strip()
    if not value or value.lower() in {"unknown", "n/a"}:
        return None
    days = 0
    if "-" in value:
        day_part, value = value.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            hours = 0
            minutes, seconds = (int(part) for part in parts)
        elif len(parts) == 3:
            hours, minutes, seconds = (int(part) for part in parts)
        else:
            return None
    except ValueError:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_slurm_task_status(stdout: str) -> SlurmTaskStatus | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            return SlurmTaskStatus(
                state=parts[1],
                elapsed_seconds=parse_slurm_duration(parts[2]),
                raw=line,
            )
        if len(parts) == 2:
            return SlurmTaskStatus(
                state=parts[0],
                elapsed_seconds=parse_slurm_duration(parts[1]),
                raw=line,
            )
    return None


def query_slurm_task_status(job_id: str, task_index: int) -> SlurmTaskStatus | None:
    task_ref = f"{job_id}_{task_index}"
    sacct = _run_optional_command(
        [
            "sacct",
            "-P",
            "-n",
            "-j",
            task_ref,
            "--format=JobIDRaw,State,Elapsed",
        ]
    )
    if sacct["returncode"] == 0:
        status = parse_slurm_task_status(str(sacct["stdout"]))
        if status is not None:
            return status
    squeue = _run_optional_command(["squeue", "-h", "-j", task_ref, "-o", "%T|%M"])
    if squeue["returncode"] == 0:
        return parse_slurm_task_status(str(squeue["stdout"]))
    return None


def query_slurm_array_job_id(job_name: str) -> str | None:
    """Reconcile the crash window after sbatch and before journal persistence."""
    squeue = _run_optional_command(
        ["squeue", "-h", "-n", job_name, "-o", "%A"]
    )
    def job_ids(stdout):
        return {line.strip().split("_", 1)[0] for line in str(stdout).splitlines()
                if line.strip().split("_", 1)[0].isdigit()}

    queued = job_ids(squeue["stdout"]) if squeue["returncode"] == 0 else set()
    if len(queued) > 1:
        raise SlurmLookupUnavailable("Multiple Slurm jobs match the submission identity")
    if queued:
        return next(iter(queued))
    sacct = _run_optional_command(
        ["sacct", "-X", "-n", "--name", job_name, "--format=JobID"]
    )
    accounted = job_ids(sacct["stdout"]) if sacct["returncode"] == 0 else set()
    if len(accounted) > 1:
        raise SlurmLookupUnavailable("Multiple accounted Slurm jobs match the submission identity")
    if accounted:
        return next(iter(accounted))
    if squeue["returncode"] != 0 or sacct["returncode"] != 0:
        raise SlurmLookupUnavailable(
            "Slurm submission lookup unavailable: "
            f"squeue={squeue['returncode']} {squeue['stderr']}; "
            f"sacct={sacct['returncode']} {sacct['stderr']}"
        )
    return None


def collect_slurm_resource_snapshot(job_id: str | None = None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    fairshare = _run_optional_command(["ulhpcshare"])
    snapshot["ulhpcshare_returncode"] = fairshare["returncode"]
    snapshot["ulhpcshare_stdout"] = fairshare["stdout"]
    snapshot["ulhpcshare_stderr"] = fairshare["stderr"]
    if job_id:
        sacct = _run_optional_command(
            [
                "sacct",
                "-j",
                job_id,
                "--format=JobID,JobName,State,Elapsed,AllocCPUS,TotalCPU,ReqMem,MaxRSS",
            ]
        )
        snapshot["sacct_returncode"] = sacct["returncode"]
        snapshot["sacct_stdout"] = sacct["stdout"]
        snapshot["sacct_stderr"] = sacct["stderr"]
    return snapshot


def submit_slurm_array(script_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["sbatch", str(script_path)], capture_output=True,
            text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise SlurmSubmissionUnknown("sbatch receipt timed out; acceptance is unknown") from exc
    receipt = re.search(r"\bSubmitted batch job\s+(\d+)\b", result.stdout or "")
    if receipt:
        return receipt.group(1)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:1000]
        rejected = any(marker in detail.lower() for marker in (
            "invalid partition", "invalid account", "invalid qos", "invalid job array",
            "access denied", "permission denied", "invalid generic resource",
            "memory specification can not be satisfied", "requested node configuration is not available",
        ))
        # An unrecognized client/transport failure is not proof of rejection.
        error = SlurmSubmissionRejected if rejected else SlurmSubmissionUnknown
        raise error("sbatch failed: " + detail)
    output = (result.stdout or "").strip()
    parts = output.split()
    return parts[-1] if parts and parts[-1].isdigit() else None
