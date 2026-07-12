"""Config-driven evaluator dispatch for online rollouts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.environment.docker_env import DockerCapacityWindow
from src.evaluator.swe_apptainer_evaluator import evaluate_apptainer
from src.evaluator.swe_evaluator import evaluate as evaluate_docker
from src.optimization.online_config import OnlineOptimizationConfig


def evaluate_online_patch(
    patch: str,
    instance_info: dict[str, Any],
    *,
    config: OnlineOptimizationConfig,
    capacity_window: DockerCapacityWindow,
    phase_workdir: Path,
    persistent_log_root: Path | None = None,
    run_id_suffix: str = "_online_gepa",
) -> dict[str, Any]:
    """Evaluate an online rollout patch using the configured backend."""
    if config.evaluator.backend == "swebench_docker":
        return evaluate_docker(
            patch,
            instance_info,
            timeout=config.evaluator.timeout,
            run_id_suffix=run_id_suffix,
        )
    if config.evaluator.backend == "swebench_apptainer":
        return evaluate_apptainer(
            patch,
            instance_info,
            container=config.container,
            capacity_window=capacity_window,
            workdir=config.docker.workdir,
            timeout=config.evaluator.timeout,
            run_id_suffix=run_id_suffix,
            phase_workdir=phase_workdir,
            persistent_log_root=persistent_log_root,
        )
    raise ValueError(f"unsupported evaluator backend: {config.evaluator.backend!r}")
