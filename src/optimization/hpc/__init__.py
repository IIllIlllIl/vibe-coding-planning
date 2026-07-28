"""Shared Slurm runtime primitives for Online and Offline GEPA."""

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.slurm import (
    SlurmTaskStatus,
    collect_slurm_resource_snapshot,
    normalize_slurm_state,
    parse_slurm_duration,
    parse_slurm_task_status,
    query_slurm_array_job_id,
    query_slurm_task_status,
    submit_slurm_array,
)

__all__ = [
    "HPCConfig",
    "SlurmTaskStatus",
    "collect_slurm_resource_snapshot",
    "normalize_slurm_state",
    "parse_slurm_duration",
    "parse_slurm_task_status",
    "query_slurm_array_job_id",
    "query_slurm_task_status",
    "submit_slurm_array",
]
