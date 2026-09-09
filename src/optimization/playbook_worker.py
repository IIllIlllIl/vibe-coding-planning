"""Run one reject-playbook Agent as an independent Slurm task."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import yaml

from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook_runtime import PromptModel, _render
from src.optimization.playbook import (
    RejectPlaybook, apply_curator_operations, apply_refiner_operations,
    validate_reflector_review,
)


def run_task(*, config_path: Path, manifest_path: Path, output_path: Path, attempt_dir: Path) -> int:
    started = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    stage = "input_load"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        role = str(manifest["role"])
        if role not in {"checker", "reflector", "curator", "refiner"}:
            raise ValueError("unsupported playbook worker role")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        prompts_path = Path(config["inputs"]["prompt_bundle"])
        if not prompts_path.is_absolute():
            prompts_path = config_path.resolve().parents[1] / prompts_path
        prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))
        stage = "prompt_render"
        user = _render(prompts[f"{role}_instance"], **manifest["prompt_values"])
        stage = "agent_execution"
        output, trajectory = PromptModel(config["models"][role])(
            prompts[f"{role}_system"], user
        )
        stage = "agent_output_validation"
        values = manifest["prompt_values"]
        if role == "reflector":
            playbook = RejectPlaybook.parse(values["internal_playbook"])
            bundle = json.loads(values["reflection_case_bundle"])
            validate_reflector_review(output, instance_id=str(bundle["instance_id"]), playbook=playbook)
        elif role == "curator":
            apply_curator_operations(RejectPlaybook.parse(values["counted_internal_playbook"]), output)
        elif role == "refiner":
            apply_refiner_operations(RejectPlaybook.parse(values["internal_playbook"]), output)
        stage = "output_write"
        atomic_json(output_path, {
            "schema_version": 1, "status": "completed", "role": role,
            "fingerprint": manifest["fingerprint"],
            "task_index": manifest["task_index"],
            "instance_id": manifest.get("instance_id"),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "agent_output": output, "trajectory": trajectory,
        })
        return 0
    except Exception as exc:
        failure = {
            "schema_version": 1, "status": "agent_failed",
            "role": manifest.get("role"), "fingerprint": manifest.get("fingerprint"),
            "task_index": manifest.get("task_index"),
            "instance_id": manifest.get("instance_id"),
            "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_stage": stage, "failure_kind": "operational",
            "error_type": type(exc).__name__, "error": str(exc),
        }
        atomic_json(attempt_dir / "failure.json", failure)
        atomic_json(output_path, failure)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--attempt-dir", required=True, type=Path)
    args = parser.parse_args()
    return run_task(config_path=args.config, manifest_path=args.manifest,
                    output_path=args.output, attempt_dir=args.attempt_dir)


if __name__ == "__main__":
    raise SystemExit(main())
