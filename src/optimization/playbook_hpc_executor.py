"""Fingerprint-bound Slurm Agent waves for reject-playbook GEPA."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
from typing import Any, Mapping, Sequence

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskFiles, atomic_json
from src.optimization.playbook import (
    PlaybookBullet,
    RejectPlaybook,
    apply_curator_operations,
    apply_refiner_operations,
    validate_bullet_token_limit,
    validate_checker_result,
    validate_reflector_review,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


class PlaybookHPCExecutor:
    def __init__(self, *, config_path: Path, run_dir: Path, hpc: HPCConfig,
                 token_counter=None, maximum_bullet_tokens: int | None = None) -> None:
        self.config_path = config_path
        self.run_dir = run_dir
        self.hpc = hpc
        self.runtime = SlurmTaskBatch(hpc)
        self.token_counter = token_counter
        self.maximum_bullet_tokens = maximum_bullet_tokens

    def batch_dir_for(self, role: str, items: Sequence[Mapping[str, Any]]) -> Path:
        semantic = {
            "schema": 1, "role": role,
            "config_sha256": hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
            "items": list(items),
        }
        return self.run_dir / "hpc_tasks" / role / _sha(semantic)

    def run_wave(self, role: str, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        semantic = {
            "schema": 1, "role": role,
            "config_sha256": hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
            "items": list(items),
        }
        fingerprint = _sha(semantic)
        batch_dir = self.batch_dir_for(role, items)
        tasks = []
        for index, item in enumerate(items):
            manifest = batch_dir / "tasks" / f"task_{index:04d}.json"
            payload = {
                "schema_version": 1, "role": role, "fingerprint": fingerprint,
                "task_index": index, "instance_id": item.get("instance_id"),
                "prompt_values": dict(item["prompt_values"]),
            }
            if "validation_playbook" in item:
                payload["validation_playbook"] = item["validation_playbook"]
            if "validation_rule_count" in item:
                payload["validation_rule_count"] = item["validation_rule_count"]
            if "evidence_dir" in item:
                payload["evidence_dir"] = item["evidence_dir"]
            if manifest.is_file() and json.loads(manifest.read_text()) != payload:
                raise ValueError("playbook task manifest mismatch")
            if not manifest.exists():
                atomic_json(manifest, payload)
            tasks.append(TaskFiles(index, str(item.get("instance_id", role)), manifest,
                                   batch_dir / "outputs" / f"task_{index:04d}.json",
                                   batch_dir / "attempts" / f"task_{index:04d}"))
        atomic_json(batch_dir / "manifest.json", {
            "schema_version": 1, "role": role, "fingerprint": fingerprint,
            "task_count": len(tasks),
            "instance_ids": [item.get("instance_id") for item in items],
        })

        def script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"{role}_array_attempt_{attempt:02d}.sbatch"
            logs = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
            logs.mkdir(parents=True, exist_ok=True)
            spec = ",".join(map(str, indices))
            cap = self.hpc.max_running_array_tasks
            if cap > 0 and len(indices) > cap:
                spec += f"%{cap}"
            lines = [
                "#!/usr/bin/env bash", f"#SBATCH --job-name={self.hpc.job_name_prefix}-{role}-{fingerprint[:10]}-a{attempt}",
                f"#SBATCH --partition={self.hpc.partition}", f"#SBATCH --cpus-per-task={self.hpc.cpus_per_task}",
                f"#SBATCH --mem={self.hpc.mem}", f"#SBATCH --time={self.hpc.time}",
                f"#SBATCH --array={spec}", f"#SBATCH --output={logs}/%x-%A_%a.out",
                f"#SBATCH --error={logs}/%x-%A_%a.err", "set -euo pipefail", "set +x",
                f"ENV_FILE={shlex.quote(self.hpc.remote_env_file)}", 'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
                'source "$ENV_FILE"', 'test -n "${DEEPSEEK_API_KEY:-}" || exit 2',
                f"BATCH_DIR={shlex.quote(str(batch_dir))}", 'TASK_ID="$(printf "%04d" "$SLURM_ARRAY_TASK_ID")"',
                f"ATTEMPT={attempt}", 'ATTEMPT_ID="$(printf "%02d" "$ATTEMPT")"',
                'ATTEMPT_DIR="$BATCH_DIR/attempts/task_${TASK_ID}/attempt_${ATTEMPT_ID}"',
                'mkdir -p "$ATTEMPT_DIR"',
                *(
                    [f"module load {shlex.quote(self.hpc.container_module)}"]
                    if role in {"reflector", "curator"} else []
                ),
                f"{shlex.quote(self.hpc.python_bin)} -m src.optimization.playbook_worker "
                f"--config {shlex.quote(str(self.config_path))} "
                '--manifest "$BATCH_DIR/tasks/task_${TASK_ID}.json" '
                '--output "$BATCH_DIR/outputs/task_${TASK_ID}.json" '
                '--attempt-dir "$ATTEMPT_DIR" '
                + (
                    '--previous-output "$BATCH_DIR/failed_outputs/'
                    f'attempt_{attempt - 1:02d}/task_${{TASK_ID}}.json"'
                    if attempt > 1 else ""
                ),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint or value.get("role") != role:
                raise ValueError("playbook worker output identity mismatch")
            if value.get("task_index") != task.index or not isinstance(value.get("agent_output"), dict):
                raise ValueError("playbook worker output schema mismatch")
            if not isinstance(value.get("trajectory"), list):
                raise ValueError("playbook worker trajectory missing")
            task_manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
            agent_output = value["agent_output"]
            if role == "checker":
                playbook = RejectPlaybook(tuple(
                    PlaybookBullet(f"host-{index:05d}", "Host validation rule")
                    for index in range(1, int(task_manifest["validation_rule_count"]) + 1)
                ))
                validate_checker_result(agent_output, playbook)
            else:
                playbook = RejectPlaybook.parse(task_manifest["validation_playbook"])
            if role == "reflector":
                validate_reflector_review(
                    agent_output,
                    instance_id=task.instance_id,
                    playbook=playbook,
                )
            elif role == "curator":
                proposed = apply_curator_operations(playbook, agent_output)
                if self.maximum_bullet_tokens is not None:
                    if self.token_counter is None:
                        raise ValueError("Curator bullet cap requires a token counter")
                    validate_bullet_token_limit(
                        proposed,
                        token_counter=self.token_counter,
                        maximum_bullet_tokens=self.maximum_bullet_tokens,
                    )
            elif role == "refiner":
                apply_refiner_operations(playbook, agent_output)

        return self.runtime.run(
            batch_dir=batch_dir, fingerprint=fingerprint, tasks=tasks,
            job_name=lambda attempt: f"{self.hpc.job_name_prefix}-{role}-{fingerprint[:10]}-a{attempt}",
            write_script=script, validate_output=validate,
        )
