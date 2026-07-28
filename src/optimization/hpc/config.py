"""Method-independent Slurm configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HPCConfig:
    """Resources and transport settings shared by GEPA task executors."""

    submit: bool = False
    remote_project_dir: str = "~/hpc_runs/vibe-coding-planning"
    remote_task_dir: str = ""
    remote_env_file: str = "~/.config/vibe-coding-planning/deepseek.env"
    ulhpc_config: str = "configs/ulhpc_submit.yaml"
    partition: str = "batch"
    cpus_per_task: int = 1
    mem: str = "4G"
    time: str = "00:55:00"
    max_running_array_tasks: int = 12
    poll_interval_seconds: int = 300
    task_output_grace_seconds: int = 300
    missing_task_grace_seconds: int = 600
    max_task_attempts: int = 2
    python_module: str = "lang/Python/3.11"
    container_module: str = "tools/Apptainer"
    python_bin: str = "python3"
    job_name_prefix: str = "gepa-task"
    worker_config_path: str = ""
