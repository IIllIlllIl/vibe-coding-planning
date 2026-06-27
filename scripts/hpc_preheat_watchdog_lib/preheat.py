from __future__ import annotations

import json
import re
from dataclasses import dataclass

from scripts.hpc_preheat_watchdog_lib.command import run_command
from scripts.hpc_preheat_watchdog_lib.config import WatchdogConfig


@dataclass(frozen=True)
class SubmittedJob:
    job_id: str
    stdout_path: str | None = None
    stderr_path: str | None = None
    remote_dir: str | None = None


def _extract_submission(stdout: str) -> SubmittedJob:
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("job_id"):
            return SubmittedJob(
                job_id=str(payload["job_id"]),
                stdout_path=payload.get("stdout_path"),
                stderr_path=payload.get("stderr_path"),
                remote_dir=payload.get("remote_dir"),
            )
    match = re.search(r"Submitted job\s+(?P<job_id>[A-Za-z0-9_.-]+)", stdout)
    if match:
        return SubmittedJob(job_id=match.group("job_id"))
    raise RuntimeError("could not find submitted job id in preheat output")


def _submit_command(config: WatchdogConfig, *, role: str) -> list[str]:
    if role == "pilot":
        gepa_config = config.pilot_config_rel
        sif_cache_dir = config.pilot_sif_cache_dir
        job_name = config.pilot_job_name
        time_limit = config.pilot_time
    elif role == "full":
        gepa_config = config.full_config_rel
        sif_cache_dir = config.full_sif_cache_dir
        job_name = config.full_job_name
        time_limit = config.full_time
    else:
        raise ValueError(f"unknown preheat role: {role}")
    command = [
        "bash",
        str(config.preheat_script),
        "--config",
        gepa_config,
        "--sif-cache-dir",
        sif_cache_dir,
        "--remote-dir",
        config.remote_project_dir,
        "--remote-dataset-dir",
        config.remote_dataset_dir,
        "--job-name",
        job_name,
        "--time",
        time_limit,
        "--cpus",
        config.cpus,
        "--mem",
        config.mem,
        "--timeout",
        config.pull_timeout,
        "--max-attempts",
        config.max_pull_attempts,
        "--retry-backoff",
        config.retry_backoff,
        "--ulhpc-config",
        str(config.ulhpc_config),
    ]
    if config.submit:
        command.append("--submit")
    return command


def submit_preheat(config: WatchdogConfig, *, role: str) -> SubmittedJob:
    result = run_command(_submit_command(config, role=role))
    if result.returncode != 0:
        raise RuntimeError(
            f"preheat submit failed rc={result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return _extract_submission(result.stdout)


def expected_image_count(config_path) -> int:
    from scripts.tools.prepare_apptainer_sifs import _collect_images
    from src.optimization.config import load_optimization_config

    return len(_collect_images(load_optimization_config(config_path, require_api_keys=False)))
