"""Run one reject-playbook Agent as an independent Slurm task."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml
import litellm

from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook_runtime import (
    PlaybookAgentOutputContractError,
    PromptModel,
    _render,
    run_evidence_curator,
    run_evidence_reflector,
    run_repository_checker,
    run_repository_reflector,
)
from src.optimization.playbook import (
    PlaybookBullet, RejectPlaybook, apply_curator_operations, apply_refiner_operations,
    validate_bullet_token_limit, validate_checker_result, validate_reflector_review,
)
from src.optimization.repo_playbook import (
    validate_repo_checker_result,
    validate_repo_reflector_review,
)


def run_task(
    *,
    config_path: Path,
    manifest_path: Path,
    output_path: Path,
    attempt_dir: Path,
    previous_output_path: Path | None = None,
) -> int:
    started = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    stage = "input_load"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        role = str(manifest["role"])
        if role not in {
            "checker", "reflector", "curator", "refiner",
            "repo_checker", "repo_reflector",
        }:
            raise ValueError("unsupported playbook worker role")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        prompts_path = Path(config["inputs"]["prompt_bundle"])
        if not prompts_path.is_absolute():
            prompts_path = config_path.resolve().parents[1] / prompts_path
        prompts = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))
        values = dict(manifest["prompt_values"])
        retry_feedback = ""
        if previous_output_path is not None and previous_output_path.is_file():
            previous = json.loads(previous_output_path.read_text(encoding="utf-8"))
            retry_feedback = str(previous.get("error", ""))
        if role in {
            "checker", "reflector", "curator", "repo_checker", "repo_reflector"
        }:
            values["retry_feedback"] = retry_feedback
        if role == "repo_checker":
            checker_system = str(prompts["checker_system"])
            contract_value = config["inputs"].get("repo_checker_contract")
            if contract_value:
                contract_path = Path(str(contract_value))
                if not contract_path.is_absolute():
                    contract_path = config_path.resolve().parents[1] / contract_path
                contract_bytes = contract_path.read_bytes()
                expected_contract_sha = str(
                    config["inputs"].get("repo_checker_contract_sha256", "")
                )
                if hashlib.sha256(contract_bytes).hexdigest() != expected_contract_sha:
                    raise ValueError("Repo Checker contract fingerprint mismatch")
                contract = yaml.safe_load(contract_bytes) or {}
                if set(contract) != {"checker_contract_appendix"} or not isinstance(
                    contract["checker_contract_appendix"], str
                ):
                    raise ValueError("Repo Checker contract file is invalid")
                checker_system += (
                    "\n\n" + contract["checker_contract_appendix"].strip() + "\n"
                )
            stage = "agent_execution"
            output, trajectory = run_repository_checker(
                model_config=config["models"]["checker"],
                repository_config={
                    **config["repo_checker"],
                    "sif_cache_dir": config["container"]["sif_cache_dir"],
                },
                system=checker_system,
                instance_template=prompts["checker_instance"],
                repository=manifest["repository"],
                image_authority=manifest["image_authority"],
                attempt_dir=attempt_dir,
                issue=str(values["issue"]),
                plan=str(values["plan"]),
                checker_visible_playbook=str(values["checker_visible_playbook"]),
                retry_feedback=str(values["retry_feedback"]),
            )
        elif role == "repo_reflector":
            source_access_issue = manifest.get("source_access_issue")
            if (
                not isinstance(source_access_issue, str)
                or not source_access_issue.strip()
            ):
                raise ValueError(
                    "Repo Reflector manifest requires a non-empty "
                    "source_access_issue"
                )
            stage = "agent_execution"
            output, trajectory = run_repository_reflector(
                model_config=config["models"]["reflector"],
                repository_config={
                    **config["repo_checker"],
                    "sif_cache_dir": config["container"]["sif_cache_dir"],
                },
                system=prompts["reflector_system"],
                instance_template=prompts["reflector_instance"],
                repository=manifest["repository"],
                image_authority=manifest["image_authority"],
                attempt_dir=attempt_dir,
                evidence_dir=str(manifest["evidence_dir"]),
                internal_playbook=str(values["internal_playbook"]),
                source_access_issue=source_access_issue,
                retry_feedback=str(values["retry_feedback"]),
            )
        elif role == "reflector":
            stage = "agent_execution"
            output, trajectory = run_evidence_reflector(
                model_config=config["models"][role],
                reflection_config=config["reflection"],
                system=prompts[f"{role}_system"],
                instance_template=prompts[f"{role}_instance"],
                evidence_dir=str(manifest["evidence_dir"]),
                internal_playbook=str(values["internal_playbook"]),
                retry_feedback=str(values["retry_feedback"]),
            )
        elif role == "curator" and "evidence_dir" in manifest:
            stage = "agent_execution"
            output, trajectory = run_evidence_curator(
                model_config=config["models"][role],
                reflection_config=config["reflection"],
                system=prompts[f"{role}_system"],
                instance_template=prompts[f"{role}_instance"],
                evidence_dir=str(manifest["evidence_dir"]),
                counted_internal_playbook=str(values["counted_internal_playbook"]),
                case_count=int(values["case_count"]),
                retry_feedback=str(values["retry_feedback"]),
            )
        else:
            stage = "prompt_render"
            user = _render(prompts[f"{role}_instance"], **values)
            if role == "curator" and retry_feedback:
                user += (
                    "\n<host_validation_feedback>"
                    + retry_feedback
                    + "</host_validation_feedback>\n"
                    "Return a complete corrected Curator JSON object.\n"
                )
            stage = "agent_execution"
            output, trajectory = PromptModel(config["models"][role])(
                prompts[f"{role}_system"], user
            )
        raw_completion = {
            "schema_version": 1,
            "status": "agent_completed",
            "role": role,
            "fingerprint": manifest["fingerprint"],
            "task_index": manifest["task_index"],
            "instance_id": manifest.get("instance_id"),
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "agent_output": output,
            "trajectory": trajectory,
        }
        atomic_json(attempt_dir / "agent_completion.json", raw_completion)
        stage = "agent_output_validation"
        if role in {"checker", "repo_checker"}:
            playbook = RejectPlaybook(tuple(
                PlaybookBullet(f"host-{index:05d}", "Host validation rule")
                for index in range(1, int(manifest["validation_rule_count"]) + 1)
            ))
            if role == "checker":
                validate_checker_result(output, playbook)
            else:
                validate_repo_checker_result(output, playbook)
        else:
            playbook = RejectPlaybook.parse(manifest["validation_playbook"])
        if role == "reflector":
            validate_reflector_review(
                output,
                instance_id=str(manifest["instance_id"]),
                playbook=playbook,
            )
        elif role == "repo_reflector":
            validate_repo_reflector_review(
                output,
                instance_id=str(manifest["instance_id"]),
                playbook=playbook,
            )
        elif role == "curator":
            proposed = apply_curator_operations(playbook, output)
            validate_bullet_token_limit(
                proposed,
                token_counter=lambda text: int(litellm.token_counter(
                    model=str(config["models"]["checker"]["model"]), text=text
                )),
                maximum_bullet_tokens=int(config["length"]["maximum_bullet_tokens"]),
            )
        elif role == "refiner":
            apply_refiner_operations(playbook, output)
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
        if isinstance(exc, PlaybookAgentOutputContractError):
            atomic_json(attempt_dir / "agent_completion.json", {
                "schema_version": 1,
                "status": "agent_completed",
                "role": manifest.get("role"),
                "fingerprint": manifest.get("fingerprint"),
                "task_index": manifest.get("task_index"),
                "instance_id": manifest.get("instance_id"),
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "agent_output_raw": getattr(exc, "raw_response", ""),
                "trajectory": getattr(exc, "trajectory", []),
            })
        elif hasattr(exc, "trajectory"):
            atomic_json(
                attempt_dir / "agent_trajectory.json",
                {
                    "schema_version": 1,
                    "status": "agent_failed",
                    "messages": getattr(exc, "trajectory"),
                },
            )
        failure = {
            "schema_version": 1, "status": "agent_failed",
            "role": manifest.get("role"), "fingerprint": manifest.get("fingerprint"),
            "task_index": manifest.get("task_index"),
            "instance_id": manifest.get("instance_id"),
            "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "failure_stage": stage,
            "failure_kind": (
                "agent_output_contract"
                if stage == "agent_output_validation"
                or isinstance(exc, PlaybookAgentOutputContractError)
                else "operational"
            ),
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
    parser.add_argument("--previous-output", type=Path)
    args = parser.parse_args()
    return run_task(config_path=args.config, manifest_path=args.manifest,
                    output_path=args.output, attempt_dir=args.attempt_dir,
                    previous_output_path=args.previous_output)


if __name__ == "__main__":
    raise SystemExit(main())
