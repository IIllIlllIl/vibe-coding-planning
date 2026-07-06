"""HPC/Slurm executor support for online GEPA rollout batches.

This module deliberately keeps GEPA state local to the controller. It only
materializes rollout task manifests and provides a collection path for worker
outputs produced by a Slurm job array.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Callable, Sequence

from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.online_models import OnlineGEPACase, OnlineRolloutOutput


@dataclass(frozen=True)
class OnlineRolloutTask:
    index: int
    case: OnlineGEPACase
    rules_path: Path
    manifest_path: Path
    output_path: Path
    worker_run_dir: Path


class OnlineRolloutBatchStore:
    """Create local task manifests and collect worker output JSON files."""

    def __init__(self, run_dir: Path) -> None:
        self.root = run_dir / "hpc_rollout_batches"
        self.root.mkdir(parents=True, exist_ok=True)

    def next_batch_dir(self) -> Path:
        existing = [
            int(path.name.rsplit("_", 1)[-1])
            for path in self.root.glob("batch_*")
            if path.is_dir() and path.name.rsplit("_", 1)[-1].isdigit()
        ]
        return self.root / f"batch_{max(existing, default=0) + 1:04d}"

    def create(
        self,
        *,
        batch: Sequence[OnlineGEPACase],
        rules: str,
        split: str | None,
        capture_traces: bool,
    ) -> tuple[Path, list[OnlineRolloutTask]]:
        batch_dir = self.next_batch_dir()
        task_dir = batch_dir / "tasks"
        output_dir = batch_dir / "outputs"
        worker_run_root = batch_dir / "worker_runs"
        task_dir.mkdir(parents=True)
        output_dir.mkdir()
        worker_run_root.mkdir()
        rules_path = batch_dir / "candidate_rules.txt"
        rules_path.write_text(rules, encoding="utf-8")
        candidate_sha256 = text_sha256(rules)
        tasks: list[OnlineRolloutTask] = []
        for index, case in enumerate(batch):
            manifest_path = task_dir / f"task_{index:04d}.json"
            output_path = output_dir / f"task_{index:04d}.json"
            worker_run_dir = worker_run_root / f"task_{index:04d}"
            payload = {
                "index": index,
                "mode": "online_planning",
                "instance_id": case.instance_id,
                "split": case.split,
                "issue_description": case.issue_description,
                "repository": {
                    "repo": case.repository.repo,
                    "base_commit": case.repository.base_commit,
                    "instance_id": case.repository.instance_id,
                },
                "rules_path": str(rules_path),
                "candidate_sha256": candidate_sha256,
                "capture_traces": capture_traces,
            }
            manifest_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            tasks.append(
                OnlineRolloutTask(
                    index=index,
                    case=case,
                    rules_path=rules_path,
                    manifest_path=manifest_path,
                    output_path=output_path,
                    worker_run_dir=worker_run_dir,
                )
            )
        (batch_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "mode": "online_planning_hpc_batch",
                    "candidate_sha256": candidate_sha256,
                    "split": split,
                    "capture_traces": capture_traces,
                    "task_count": len(tasks),
                    "instances": [case.instance_id for case in batch],
                },
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return batch_dir, tasks

    @staticmethod
    def load_output(path: Path) -> OnlineRolloutOutput:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "completed":
            raise RuntimeError(
                f"online rollout worker failed for {data.get('instance_id')}: "
                f"{data.get('error_type')}: {data.get('error')}"
            )
        return OnlineRolloutOutput(
            resolved=bool(data["resolved"]),
            plan=str(data["plan"]),
            patch=str(data["patch"]),
            plan_trajectory=tuple(data.get("plan_trajectory", [])),
            code_trajectory=tuple(data.get("code_trajectory", [])),
            evaluator_result=dict(data.get("evaluator_result", {})),
            attribution_hint=dict(data.get("attribution_hint", {})),
        )


def build_slurm_array_script(
    *,
    config_path: str,
    batch_dir: str,
    task_count: int,
    job_name: str,
    partition: str,
    cpus_per_task: int,
    mem: str,
    time_limit: str,
    max_running_array_tasks: int,
    remote_env_file: str,
    python_module: str,
    container_module: str,
    python_bin: str,
) -> str:
    if task_count < 1:
        raise ValueError("task_count must be positive")
    array_spec = f"0-{task_count - 1}%{max_running_array_tasks}"
    quoted_config = shlex.quote(config_path)
    quoted_batch_dir = shlex.quote(batch_dir)
    quoted_python = shlex.quote(python_bin)
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --cpus-per-task={cpus_per_task}",
        f"#SBATCH --mem={mem}",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --array={array_spec}",
        "#SBATCH --output=%x-%A_%a.out",
        "#SBATCH --error=%x-%A_%a.err",
        "set -euo pipefail",
        "set +x",
        f"module load {shlex.quote(python_module)}",
        f"module load {shlex.quote(container_module)}",
        f"ENV_FILE={shlex.quote(remote_env_file)}",
        'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
        'source "${ENV_FILE}"',
        'test -n "${DEEPSEEK_API_KEY:-}" || { echo "missing DEEPSEEK_API_KEY" >&2; exit 2; }',
        f"BATCH_DIR={quoted_batch_dir}",
        'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"',
        'TASK_MANIFEST="${BATCH_DIR}/tasks/task_${TASK_ID}.json"',
        'OUTPUT_JSON="${BATCH_DIR}/outputs/task_${TASK_ID}.json"',
        'WORKER_RUN_DIR="${BATCH_DIR}/worker_runs/task_${TASK_ID}"',
        f"{quoted_python} -m src.optimization.online_rollout_worker "
        f"--config {quoted_config} "
        "--task-manifest \"${TASK_MANIFEST}\" "
        "--output \"${OUTPUT_JSON}\" "
        "--worker-run-dir \"${WORKER_RUN_DIR}\"",
    ]
    return "\n".join(lines) + "\n"


class HPCSlurmOnlineRolloutExecutor:
    """Prepare, optionally submit, and collect a Slurm rollout batch."""

    def __init__(
        self,
        config: OnlineOptimizationConfig,
        *,
        submitter: Callable[[Path], None] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.store = OnlineRolloutBatchStore(config.run_dir)
        self.submitter = submitter
        self.sleep = sleeper
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    def evaluate(
        self,
        batch: list[OnlineGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[OnlineRolloutOutput]:
        split = next((case.split for case in batch), None)
        batch_dir, tasks = self.store.create(
            batch=batch,
            rules=rules,
            split=split,
            capture_traces=capture_traces,
        )
        script = build_slurm_array_script(
            config_path=self.config.hpc.worker_config_path,
            batch_dir=str(batch_dir),
            task_count=len(tasks),
            job_name=f"{self.config.hpc.job_name_prefix}-{batch_dir.name}",
            partition=self.config.hpc.partition,
            cpus_per_task=self.config.hpc.cpus_per_task,
            mem=self.config.hpc.mem,
            time_limit=self.config.hpc.time,
            max_running_array_tasks=self.config.hpc.max_running_array_tasks,
            remote_env_file=self.config.hpc.remote_env_file,
            python_module=self.config.hpc.python_module,
            container_module=self.config.hpc.container_module,
            python_bin=self.config.hpc.python_bin,
        )
        script_path = batch_dir / "rollout_array.sbatch"
        script_path.write_text(script, encoding="utf-8")
        self.audit.write(
            "online_hpc_rollout_batch_prepared",
            batch_dir=str(batch_dir),
            task_count=len(tasks),
            submit=self.config.hpc.submit,
            cpus_per_task=self.config.hpc.cpus_per_task,
            mem=self.config.hpc.mem,
            time=self.config.hpc.time,
            max_running_array_tasks=self.config.hpc.max_running_array_tasks,
        )
        if self.config.hpc.submit:
            (self.submitter or submit_slurm_array)(script_path)
            self._wait_for_outputs(tasks)
            return [self.store.load_output(task.output_path) for task in tasks]
        raise RuntimeError(
            "HPC rollout batch prepared but not submitted. Review "
            f"{script_path} and set hpc.submit=true after resource pilot setup."
        )

    def _wait_for_outputs(self, tasks: list[OnlineRolloutTask]) -> None:
        remaining = {task.output_path for task in tasks}
        while remaining:
            complete = {path for path in remaining if path.is_file()}
            remaining -= complete
            if remaining:
                self.sleep(self.config.hpc.poll_interval_seconds)


def submit_slurm_array(script_path: Path) -> None:
    """Submit a prepared Slurm array script with ``sbatch``.

    This assumes the controller is running on a host where ``sbatch`` can see
    the task directory paths used in the script. For local Mac controllers,
    keep ``hpc.submit=false`` and use the generated script as the artifact
    passed to a site-specific sync/submission wrapper.
    """

    result = subprocess.run(
        ["sbatch", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "sbatch failed: " + (result.stderr or result.stdout).strip()[:1000]
        )
