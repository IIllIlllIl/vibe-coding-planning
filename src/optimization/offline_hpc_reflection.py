"""Offline Reflection proposer with separate initial and repair Slurm tasks."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shlex
from typing import Any, Mapping, Sequence

from src.exceptions import OfflineReflectionBlocked
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.config import OptimizationConfig
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskAttemptsExhausted,
    TaskFiles,
    atomic_json,
)
from src.optimization.reflection import find_candidate_contamination


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class HPCOfflineReflectionProposer:
    def __init__(
        self,
        config: OptimizationConfig,
        *,
        successful_proposals: int = 0,
        failures: Sequence[Mapping[str, str]] = (),
    ) -> None:
        self.config = config
        self.root = config.run_dir / "hpc_tasks" / "reflection"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = SlurmTaskBatch(config.hpc)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.errors = JsonlLogger(config.run_dir / "errors.jsonl")
        self.successful_proposals = successful_proposals
        self.failures = [dict(value) for value in failures]

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("GEPA may only update the rules component")
        behavioral = self.config.task.semantics == "behavioral_plan_acceptability_v1"
        mode = "behavioral_reflection" if behavioral else "offline_reflection"
        fingerprint = _stable_sha256(
            {
                "schema": 1,
                "mode": mode,
                "candidate": candidate,
                "reflective_dataset": reflective_dataset,
                "components_to_update": components_to_update,
                "reflection": asdict(self.config.reflection),
                "container": {
                    **asdict(self.config.container),
                    "sif_cache_dir": str(
                        self.config.container.sif_cache_dir
                    ),
                },
                "reflection_prompt": self.config.reflection_prompt,
                "reflection_instance_template": (
                    self.config.reflection_instance_template
                ),
            }
        )
        task_dir = self.root / fingerprint
        manifest_path = task_dir / "input.json"
        output_path = task_dir / "result.json"
        attempts_dir = task_dir / "attempts" / "task_0000"
        task_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "mode": mode,
            "fingerprint": fingerprint,
            "candidate": candidate,
            "reflective_dataset": reflective_dataset,
            "components_to_update": components_to_update,
        }
        if manifest_path.exists():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                raise ValueError("Offline Reflection task manifest mismatch")
        else:
            atomic_json(manifest_path, payload)
        task = TaskFiles(
            index=0,
            instance_id="reflection",
            manifest_path=manifest_path,
            output_path=output_path,
            attempts_dir=attempts_dir,
        )

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            if list(indices) != [0]:
                raise ValueError("Offline Reflection has exactly one task")
            path = task_dir / f"reflection_attempt_{attempt:02d}.sbatch"
            path.write_text(
                self._script(task_dir, attempt),
                encoding="utf-8",
            )
            return path

        def validate(_: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint:
                raise ValueError("Offline Reflection output fingerprint mismatch")
            outcome = value.get("outcome")
            if outcome == "proposal":
                self._validate_proposal(value.get("proposal"))
                return
            if outcome != "repair_required":
                raise ValueError("Offline Reflection output outcome is invalid")
            if not isinstance(value.get("proposed_rules"), str):
                raise ValueError("repair-required rules are invalid")
            if not isinstance(value.get("contamination_hits"), list):
                raise ValueError("repair-required hits are invalid")
            if not isinstance(value.get("instance_ids"), list):
                raise ValueError("repair-required instance IDs are invalid")
            records = list(reflective_dataset["rules"])
            expected_hits = find_candidate_contamination(
                str(value["proposed_rules"]),
                records,
            )
            if value["contamination_hits"] != expected_hits:
                raise ValueError("repair-required contamination hits mismatch")
            expected_ids = [
                str(record["instance_id"]) for record in records
            ]
            if value["instance_ids"] != expected_ids:
                raise ValueError("repair-required instance IDs mismatch")
            bundle = Path(str(value.get("evidence_bundle", ""))).resolve()
            try:
                bundle.relative_to(task_dir.resolve())
            except ValueError as exc:
                raise ValueError(
                    "repair evidence bundle is outside its initial task"
                ) from exc
            if not (bundle / "manifest.json").is_file():
                raise ValueError("repair evidence bundle is incomplete")

        try:
            outputs = self.runtime.run(
                batch_dir=task_dir,
                fingerprint=fingerprint,
                tasks=[task],
                job_name=lambda attempt: (
                    f"{self.config.hpc.job_name_prefix}-reflection-"
                    f"{fingerprint[:12]}-a{attempt}"
                ),
                write_script=write_script,
                validate_output=validate,
            )
        except TaskAttemptsExhausted as exc:
            failure = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "candidate_sha256": text_sha256(candidate["rules"]),
                "outcome": "proposal_failed_retry_new_minibatch",
            }
            self.failures.append(failure)
            self.audit.write("reflection_failed", **failure)
            self.errors.write("reflection_failed", **failure)
            raise
        except Exception as exc:
            failure = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "candidate_sha256": text_sha256(candidate["rules"]),
            }
            self.failures.append(failure)
            self.audit.write("reflection_failed", **failure)
            self.errors.write("reflection_failed", **failure)
            raise OfflineReflectionBlocked(exc) from exc
        initial = outputs[0]
        if initial["outcome"] == "repair_required":
            try:
                proposal = self._run_repair(
                    initial_task_dir=task_dir,
                    initial_fingerprint=fingerprint,
                    initial_output=initial,
                )
            except TaskAttemptsExhausted as exc:
                failure = {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "candidate_sha256": text_sha256(candidate["rules"]),
                    "proposal_stage": "contamination_repair",
                    "outcome": "proposal_failed_retry_new_minibatch",
                }
                self.failures.append(failure)
                self.audit.write("reflection_failed", **failure)
                self.errors.write("reflection_failed", **failure)
                raise
            except Exception as exc:
                failure = {
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "candidate_sha256": text_sha256(candidate["rules"]),
                    "proposal_stage": "contamination_repair",
                }
                self.failures.append(failure)
                self.audit.write("reflection_failed", **failure)
                self.errors.write("reflection_failed", **failure)
                raise OfflineReflectionBlocked(exc) from exc
        else:
            proposal = dict(initial["proposal"])
        self.successful_proposals += 1
        return proposal

    @staticmethod
    def _validate_proposal(value: Any) -> None:
        if (
            not isinstance(value, dict)
            or set(value) != {"rules"}
            or not isinstance(value["rules"], str)
        ):
            raise ValueError("Offline Reflection output proposal is invalid")

    def _run_repair(
        self,
        *,
        initial_task_dir: Path,
        initial_fingerprint: str,
        initial_output: Mapping[str, Any],
    ) -> dict[str, str]:
        repair_fingerprint = _stable_sha256(
            {
                "schema": 1,
                "mode": "offline_reflection_repair",
                "initial_fingerprint": initial_fingerprint,
                "proposed_rules": initial_output["proposed_rules"],
                "contamination_hits": initial_output["contamination_hits"],
                "instance_ids": initial_output["instance_ids"],
            }
        )
        task_dir = (
            initial_task_dir / "repair_tasks" / repair_fingerprint
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = task_dir / "input.json"
        output_path = task_dir / "result.json"
        payload = {
            "schema_version": 1,
            "mode": "offline_reflection_repair",
            "fingerprint": repair_fingerprint,
            "initial_fingerprint": initial_fingerprint,
            # These worker locators are relative to this repair manifest so
            # they remain stable across independent controller snapshots.
            "source_manifest": os.path.relpath(
                (initial_task_dir / "input.json").resolve(),
                start=manifest_path.parent.resolve(),
            ),
            "evidence_bundle": os.path.relpath(
                Path(str(initial_output["evidence_bundle"])).resolve(),
                start=manifest_path.parent.resolve(),
            ),
            "proposed_rules": initial_output["proposed_rules"],
            "contamination_hits": initial_output["contamination_hits"],
            "instance_ids": initial_output["instance_ids"],
        }
        if manifest_path.is_file():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                raise ValueError("Offline Reflection repair manifest mismatch")
        else:
            atomic_json(manifest_path, payload)
        task = TaskFiles(
            index=0,
            instance_id="reflection_repair",
            manifest_path=manifest_path,
            output_path=output_path,
            attempts_dir=task_dir / "attempts" / "task_0000",
        )

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            if list(indices) != [0]:
                raise ValueError("Offline Reflection repair has one task")
            path = task_dir / f"repair_attempt_{attempt:02d}.sbatch"
            path.write_text(
                self._script(task_dir, attempt, phase="reflection-repair"),
                encoding="utf-8",
            )
            return path

        def validate(_: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != repair_fingerprint:
                raise ValueError("Offline Reflection repair fingerprint mismatch")
            if value.get("outcome") != "proposal":
                raise ValueError("Offline Reflection repair outcome is invalid")
            self._validate_proposal(value.get("proposal"))
            repaired_rules = str(value["proposal"]["rules"])
            source = json.loads(
                (initial_task_dir / "input.json").read_text(encoding="utf-8")
            )
            records = list(source["reflective_dataset"]["rules"])
            remaining = find_candidate_contamination(repaired_rules, records)
            if remaining:
                raise ValueError(
                    "Offline Reflection repair output remains contaminated"
                )
            if repaired_rules.strip() == str(
                source["candidate"]["rules"]
            ).strip():
                raise ValueError(
                    "Offline Reflection repair output equals the parent"
                )

        outputs = self.runtime.run(
            batch_dir=task_dir,
            fingerprint=repair_fingerprint,
            tasks=[task],
            job_name=lambda attempt: (
                f"{self.config.hpc.job_name_prefix}-reflection-repair-"
                f"{repair_fingerprint[:12]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
        self.audit.write(
            "offline_hpc_reflection_repair_completed",
            initial_fingerprint=initial_fingerprint,
            repair_fingerprint=repair_fingerprint,
            task_dir=str(task_dir),
        )
        return dict(outputs[0]["proposal"])

    def _script(
        self,
        task_dir: Path,
        attempt: int,
        *,
        phase: str = "reflection",
    ) -> str:
        hpc = self.config.hpc
        config_path = hpc.worker_config_path
        if not config_path:
            raise ValueError("hpc.worker_config_path is required")
        job_name = (
            f"{hpc.job_name_prefix}-{phase}-{task_dir.name[:12]}-a{attempt}"
        )
        slurm_log_dir = task_dir / "slurm_logs" / f"attempt_{attempt:02d}"
        slurm_log_dir.mkdir(parents=True, exist_ok=True)
        attempt_dir = task_dir / "attempts" / "task_0000" / (
            f"attempt_{attempt:02d}"
        )
        behavioral = self.config.task.semantics == "behavioral_plan_acceptability_v1"
        module_lines = [] if behavioral else [
            f"module load {shlex.quote(hpc.python_module)}",
            f"module load {shlex.quote(hpc.container_module)}",
        ]
        worker_module = (
            "src.optimization.behavioral_reflection_worker"
            if behavioral
            else "src.optimization.offline_reflection_worker"
        )
        lines = [
            "#!/usr/bin/env bash",
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --partition={hpc.partition}",
            f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
            f"#SBATCH --mem={hpc.mem}",
            f"#SBATCH --time={hpc.time}",
            "#SBATCH --array=0",
            f"#SBATCH --output={slurm_log_dir}/%x-%j.out",
            f"#SBATCH --error={slurm_log_dir}/%x-%j.err",
            "set -euo pipefail",
            "set +x",
            *module_lines,
            f"ENV_FILE={shlex.quote(hpc.remote_env_file)}",
            'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
            'source "${ENV_FILE}"',
            'test -n "${DEEPSEEK_API_KEY:-}" || exit 2',
            f"mkdir -p {shlex.quote(str(attempt_dir))}",
            f"{shlex.quote(hpc.python_bin)} "
            f"-m {worker_module} "
            f"--config {shlex.quote(config_path)} "
            f"--manifest {shlex.quote(str(task_dir / 'input.json'))} "
            f"--output {shlex.quote(str(task_dir / 'result.json'))} "
            f"--attempt-dir {shlex.quote(str(attempt_dir))}",
        ]
        return "\n".join(lines) + "\n"
