"""Shared Agent-wave layout and scripts, extracted from the formal playbook path.

Callers retain scientific manifests and validators. SlurmTaskBatch remains the
only owner of submission, retries, reconciliation and yield/resume state.
"""
from pathlib import Path
import json
import shlex
from typing import Any, Mapping, Sequence

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import TaskFiles, atomic_json


def prepare_phase_wave(*, batch_dir: Path, role: str, fingerprint: str,
                       manifests: Sequence[Mapping[str, Any]], instance_ids: Sequence,
                       mismatch_error: str = "phase task manifest mismatch",
                       batch_manifest: Mapping[str, Any] | None = None) -> list[TaskFiles]:
    if len(manifests) != len(instance_ids):
        raise ValueError("phase wave requires one manifest per task")
    for directory in ("tasks", "outputs", "attempts"):
        (batch_dir / directory).mkdir(parents=True, exist_ok=True)
    tasks = []
    for index, (manifest, instance_id) in enumerate(zip(manifests, instance_ids, strict=True)):
        path = batch_dir / "tasks" / f"task_{index:04d}.json"
        if path.exists() and json.loads(path.read_text()) != manifest:
            raise ValueError(mismatch_error)
        if not path.exists():
            atomic_json(path, manifest)
        tasks.append(TaskFiles(index, str(instance_id if instance_id is not None else role), path,
                               batch_dir / "outputs" / f"task_{index:04d}.json",
                               batch_dir / "attempts" / f"task_{index:04d}"))
    atomic_json(batch_dir / "manifest.json", batch_manifest if batch_manifest is not None else
                dict(schema_version=1, role=role, fingerprint=fingerprint,
                     task_count=len(tasks), instance_ids=list(instance_ids)))
    return tasks


def build_phase_array_script(*, hpc: HPCConfig, batch_dir: Path, indices: Sequence[int],
                             attempt: int, job_name: str, config_path: str | Path,
                             worker_module: str, modules: Sequence[str] = (),
                             source_env: bool = True, credential_env: str | None = "DEEPSEEK_API_KEY",
                             manifest_option: str = "--manifest", export_all: bool = False,
                             add_user_bin: bool = False) -> str:
    if not config_path:
        raise ValueError("hpc.worker_config_path is required")
    if not indices or any(type(index) is not int or index < 0 for index in indices) or len(set(indices)) != len(indices):
        raise ValueError("phase array requires unique nonnegative indices")
    spec = ",".join(map(str, indices))
    if hpc.max_running_array_tasks > 0 and len(indices) > hpc.max_running_array_tasks:
        spec += f"%{hpc.max_running_array_tasks}"
    logs = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    logs.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash", f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}", f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}", f"#SBATCH --time={hpc.time}", f"#SBATCH --array={spec}",
        *(["#SBATCH --export=ALL"] if export_all else []),
        f"#SBATCH --output={logs}/%x-%A_%a.out", f"#SBATCH --error={logs}/%x-%A_%a.err",
        "set -euo pipefail", "set +x",
        *(['export PATH="$HOME/.local/bin:$PATH"'] if add_user_bin else []),
        *[f"module load {shlex.quote(module)}" for module in modules],
        *([f"ENV_FILE={shlex.quote(hpc.remote_env_file)}", 'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
           'source "${ENV_FILE}"'] if source_env else []),
        *([f'test -n "${{{credential_env}:-}}" || exit 2'] if credential_env else []),
        f"BATCH_DIR={shlex.quote(str(batch_dir))}",
        'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"', f"ATTEMPT={attempt}",
        'ATTEMPT_ID="$(printf "%02d" "${ATTEMPT}")"',
        'TASK_MANIFEST="${BATCH_DIR}/tasks/task_${TASK_ID}.json"',
        'OUTPUT_JSON="${BATCH_DIR}/outputs/task_${TASK_ID}.json"',
        'ATTEMPT_DIR="${BATCH_DIR}/attempts/task_${TASK_ID}/attempt_${ATTEMPT_ID}"',
        'mkdir -p "${ATTEMPT_DIR}"',
        f"{shlex.quote(hpc.python_bin)} -m {worker_module} --config {shlex.quote(str(config_path))} "
        f'{manifest_option} "${{TASK_MANIFEST}}" --output "${{OUTPUT_JSON}}" --attempt-dir "${{ATTEMPT_DIR}}"'
        + (f' --previous-output "${{BATCH_DIR}}/failed_outputs/attempt_{attempt - 1:02d}/task_${{TASK_ID}}.json"'
           if attempt > 1 else ""),
    ]
    return "\n".join(lines) + "\n"
