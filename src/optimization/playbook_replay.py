"""Replay one frozen Curator scene without rerunning Checker or Reflector."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import litellm
import yaml

from src.exceptions import ControllerYield
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook import RejectPlaybook, apply_curator_operations, overlength_bullet_ids
from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_curator_replay(config_path: str | Path) -> dict[str, Any] | None:
    """Run the Curator against an exact, fingerprinted historical input."""
    config_path = Path(config_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "offline_reject_playbook_curator_replay":
        raise ValueError("not an offline_reject_playbook_curator_replay config")

    replay = raw["replay"]
    source = Path(os.path.expanduser(str(replay["source_curator_task"])))
    actual_source_sha = _sha256(source)
    if actual_source_sha != str(replay["source_curator_task_sha256"]):
        raise ValueError("frozen Curator replay input fingerprint mismatch")
    source_task = json.loads(source.read_text(encoding="utf-8"))
    counted = RejectPlaybook.parse(source_task["validation_playbook"])
    reflections = json.loads(source_task["prompt_values"]["case_reflections"])

    prompt_path = Path(str(raw["inputs"]["prompt_bundle"]))
    if not prompt_path.is_absolute():
        prompt_path = config_path.resolve().parents[1] / prompt_path
    if _sha256(prompt_path) != str(raw["inputs"]["prompt_bundle_sha256"]):
        raise ValueError("prompt bundle fingerprint mismatch")

    model = str(raw["models"]["checker"]["model"])

    def token_counter(text: str) -> int:
        return int(litellm.token_counter(model=model, text=text))

    maximum_bullet_tokens = int(raw["length"]["maximum_bullet_tokens"])
    h = raw["hpc"]
    hpc = HPCConfig(
        submit=bool(h["submit"]), partition=str(h["partition"]),
        cpus_per_task=int(h["cpus_per_task"]), mem=str(h["mem"]),
        time=str(h["agent_time"]),
        max_running_array_tasks=int(h["max_running_array_tasks"]),
        poll_interval_seconds=int(h["poll_interval_seconds"]),
        task_output_grace_seconds=int(h["task_output_grace_seconds"]),
        missing_task_grace_seconds=int(h["missing_task_grace_seconds"]),
        max_task_attempts=int(h["max_task_attempts"]),
        remote_env_file=str(h["remote_env_file"]),
        python_module=str(h.get("python_module", "lang/Python/3.11")),
        container_module=str(h.get("container_module", "tools/Apptainer")),
        python_bin=str(h.get("python_bin", "python3")),
        job_name_prefix=str(h["job_name_prefix"]),
        worker_config_path=str(config_path),
    )
    run_dir = Path(str(raw["paths"]["run_dir"]))
    executor = PlaybookHPCExecutor(
        config_path=config_path, run_dir=run_dir, hpc=hpc,
        token_counter=token_counter,
        maximum_bullet_tokens=maximum_bullet_tokens,
    )
    agents = HPCPlaybookProposalAgents(
        executor,
        maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
        maximum_bullet_tokens=maximum_bullet_tokens,
        token_counter=token_counter,
    )
    status_path = run_dir / "controller_status.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_json(status_path, {"schema_version": 1, "status": "running"})
    try:
        operations = agents.curate(counted, reflections)
    except ControllerYield as exc:
        atomic_json(status_path, {
            "schema_version": 1, "status": "yielded", "reason": exc.reason,
            "batch_dir": exc.batch_dir, "worker_job_id": exc.job_id,
        })
        return None

    proposed = apply_curator_operations(counted, operations)
    invalid_ids = overlength_bullet_ids(
        proposed, token_counter=token_counter,
        maximum_bullet_tokens=maximum_bullet_tokens,
    )
    result = {
        "schema_version": 1,
        "run_status": "completed",
        "candidate_status": "INVALID" if invalid_ids else "VALID",
        "candidate_score_if_evaluated": -100.0 if invalid_ids else None,
        "invalid_bullet_ids": invalid_ids,
        "maximum_bullet_tokens": maximum_bullet_tokens,
        "source_curator_task": str(source),
        "source_curator_task_sha256": actual_source_sha,
        "operations": operations,
        "proposed_playbook": json.loads(proposed.serialize()),
    }
    atomic_json(run_dir / "result.json", result)
    atomic_json(status_path, {"schema_version": 1, "status": "completed"})
    return result
