"""Atomic PC and CE phase runners for PolyBench PCCE."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

from src.agents import plan_agent
from src.config import AgentConfig, Config, EvaluatorConfig, PromptConfig, SystemConfig
from src.environment.apptainer_env import ApptainerEnvironment, ApptainerSifCache
from src.environment.docker_env import DockerCapacityWindow
from src.environment.repository_baseline import restore_repository_to_base
from src.exceptions import FatalError
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.checker import DockerChecker
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.models import CheckerOutput, RepositoryEvidence
from src.polybench_pcce.config import PolyBenchPCCEConfig
from src.polybench_pcce.models import CEAssignment, PCCECheckerCase, PCReviewAssignment
from src.polybench_pce.dataset import file_sha256
from src.polybench_pce.runner import PolyBenchPCERunner, checkpoint_identity


def _checkpoint(path: Path, identity: str, phase: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("checkpoint_identity") != identity or value.get("phase") != phase:
        raise FatalError(f"PCCE checkpoint identity mismatch: {path}")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise FatalError(f"invalid PCCE checkpoint payload: {path}")
    return dict(payload)


def _save_checkpoint(
    path: Path, identity: str, phase: str, payload: dict[str, Any]
) -> None:
    atomic_json(
        path,
        {
            "schema_version": 1,
            "checkpoint_identity": identity,
            "phase": phase,
            "payload": payload,
        },
    )


def _verify_sif(
    config: PolyBenchPCCEConfig,
    assignment: PCReviewAssignment | CEAssignment,
    capacity: DockerCapacityWindow,
) -> None:
    case = assignment.case.source
    expected = Path(case.image.sif_path)
    runtime = ApptainerSifCache(config.pce.container.sif_cache_dir, capacity).sif_path(
        case.image.requested_ref
    )
    if runtime != expected or not expected.is_file():
        raise FatalError(f"frozen PCCE SIF is missing or relocated: {expected}")
    if (
        expected.stat().st_size != case.image.sif_bytes
        or file_sha256(expected) != case.image.sif_sha256
    ):
        raise FatalError(f"frozen PCCE SIF identity mismatch: {expected}")


def _review_identity(
    assignment: PCReviewAssignment, fingerprint: str, guideline: str
) -> str:
    value = {
        "schema": 1,
        "fingerprint": fingerprint,
        "instance_id": assignment.case.instance_id,
        "review_index": assignment.review_index,
        "rejection_count": assignment.rejection_count,
        "input_plan_sha256": text_sha256(assignment.input_plan),
        "previous_feedback_sha256": text_sha256(assignment.previous_feedback),
        "guideline_sha256": text_sha256(guideline),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_pcce_checker_output(value: dict[str, Any]) -> CheckerOutput:
    should_proceed = value.get("should_proceed")
    reason = value.get("decision_reason")
    feedback = value.get("revision_feedback")
    evidence = value.get("repository_evidence")
    if not isinstance(should_proceed, bool):
        raise ValueError("should_proceed must be boolean")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("decision_reason must be a non-empty string")
    if not isinstance(feedback, str):
        raise ValueError("revision_feedback must be a string")
    if should_proceed and feedback.strip():
        raise ValueError("revision_feedback must be empty when should_proceed is true")
    if not should_proceed and not feedback.strip():
        raise ValueError(
            "revision_feedback must be non-empty when should_proceed is false"
        )
    if not isinstance(evidence, list):
        raise ValueError("repository_evidence must be a list")
    normalized = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("repository evidence items must be objects")
        fields = (item.get("path"), item.get("symbol"), item.get("finding"))
        if not all(isinstance(field, str) for field in fields):
            raise ValueError("repository evidence fields must be strings")
        normalized.append(RepositoryEvidence(*fields))
    return CheckerOutput(
        should_proceed,
        reason.strip(),
        tuple(normalized),
        revision_feedback=feedback.strip(),
    )


class PolyBenchPCCERunner:
    def __init__(
        self,
        config: PolyBenchPCCEConfig,
        capacity: DockerCapacityWindow,
        *,
        checkpoint_dir: Path,
        attempt_dir: Path,
    ) -> None:
        self.config = config
        self.capacity = capacity
        self.checkpoint_dir = checkpoint_dir
        self.attempt_dir = attempt_dir
        self.audit = JsonlLogger(attempt_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(attempt_dir / "usage.jsonl")

    def _plan_config(self) -> Config:
        model = self.config.pce.plan
        return Config(
            system=SystemConfig(
                n=1,
                optimization_info_level=1,
                model=model.model,
                api_base=model.api_base,
                dataset="AmazonScience/SWE-PolyBench",
                dataset_type="polybench",
                language_filter="Python",
                instances=[],
                output_dir=str(self.attempt_dir),
                batch_id="polybench_pcce_revision",
                skip_completed_rounds=True,
            ),
            prompts=PromptConfig(
                plan_generation_prompt=self.config.plan_revision_prompt,
                plan_instance_template=self.config.plan_revision_instance_template,
                code_generation_prompt=self.config.pce.code_prompt,
                code_instance_template=self.config.pce.code_instance_template,
                nrpv_block=self.config.pce.nrpv_block,
            ),
            docker=self.config.pce.docker,
            agent=AgentConfig(
                max_steps=model.max_steps,
                cost_limit=model.cost_limit,
                timeout=model.timeout,
                temperature=model.temperature,
            ),
            evaluator=EvaluatorConfig(timeout=self.config.pce.evaluator_timeout),
            api_key=os.environ[model.api_key_env],
        )

    def _environment(self, assignment: PCReviewAssignment) -> ApptainerEnvironment:
        case = assignment.case.source
        return ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=self.config.pce.docker.workdir,
            sif_cache_dir=self.config.pce.container.sif_cache_dir,
            capacity_window=self.capacity,
            timeout=self.config.pce.plan.timeout,
            writable_tmpfs=self.config.pce.container.writable_tmpfs,
            git_safe_directories=[self.config.pce.docker.workdir],
        )

    def run_pc(
        self,
        assignment: PCReviewAssignment,
        *,
        fingerprint: str,
        guideline: str,
    ) -> dict[str, Any]:
        _verify_sif(self.config, assignment, self.capacity)
        identity = _review_identity(assignment, fingerprint, guideline)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        plan_path = self.checkpoint_dir / "plan.json"
        plan_payload = _checkpoint(plan_path, identity, "plan")
        if plan_payload is None:
            if assignment.review_index == 1:
                plan_payload = {
                    "plan": assignment.input_plan,
                    "trajectory": [],
                    "source": "frozen_historical_pce",
                }
            else:
                revision_task = (
                    "<issue>\n"
                    + assignment.case.source.issue_description
                    + "\n</issue>\n\n<previous_plan>\n"
                    + assignment.input_plan
                    + "\n</previous_plan>\n\n<checker_feedback>\n"
                    + assignment.previous_feedback
                    + "\n</checker_feedback>"
                )
                env = self._environment(assignment)
                try:
                    restore_repository_to_base(
                        env,
                        assignment.case.source.base_commit,
                        phase="plan_revision",
                        evidence_dir=(
                            self.attempt_dir
                            / "repository_baselines"
                            / "plan_revision"
                        ),
                    )
                    plan, trajectory = plan_agent.run(
                        self._plan_config(),
                        revision_task,
                        env,
                        model_wrapper=lambda model: AuditedModel(
                            model,
                            self.usage,
                            phase="plan_revision",
                            context={
                                "instance_id": assignment.case.instance_id,
                                "review_index": assignment.review_index,
                                "mode": "polybench_pcce",
                            },
                        ),
                        failure_trajectory_path=self.attempt_dir / "plan_failure.json",
                    )
                    plan_payload = {
                        "plan": plan,
                        "trajectory": list(trajectory),
                        "source": "planner_revision",
                    }
                    _save_checkpoint(plan_path, identity, "plan", plan_payload)
                finally:
                    try:
                        env.cleanup()
                    except Exception as exc:
                        self.audit.write(
                            "pcce_cleanup_failed", phase="plan_revision", error=str(exc)
                        )
            if assignment.review_index == 1:
                _save_checkpoint(plan_path, identity, "plan", plan_payload)

        checker_path = self.checkpoint_dir / "checker.json"
        checker_payload = _checkpoint(checker_path, identity, "checker")
        if checker_payload is None:
            checker_config = replace(
                self.config.checker,
                run_dir=self.attempt_dir / "checker",
                checker=replace(self.config.checker.checker, max_attempts=1),
                checker_prompt=self.config.checker_prompt,
                checker_instance_template=self.config.checker_instance_template,
            )

            def save_completed_checker(output: CheckerOutput) -> None:
                _save_checkpoint(
                    checker_path,
                    identity,
                    "checker",
                    {
                        "should_proceed": output.predicted_resolved,
                        "decision_reason": output.decision_reason,
                        "revision_feedback": output.revision_feedback,
                        "repository_evidence": output.to_dict()["repository_evidence"],
                        "trajectory": list(output.trajectory),
                    },
                )

            DockerChecker(checker_config, self.capacity)(
                PCCECheckerCase(assignment.case.source, str(plan_payload["plan"]), {}),  # type: ignore[arg-type]
                guideline,
                trajectory_journal_path=self.attempt_dir / "checker_trajectory.jsonl",
                output_validator=validate_pcce_checker_output,
                completion_callback=save_completed_checker,
                repository_baseline_dir=(
                    self.attempt_dir / "repository_baselines" / "checker"
                ),
            )
            checker_payload = _checkpoint(checker_path, identity, "checker")
            if checker_payload is None:
                raise FatalError("PCCE Checker completed without a durable checkpoint")

        return {
            "pc_status": "completed",
            "review_index": assignment.review_index,
            "rejection_count_before_review": assignment.rejection_count,
            "rejection_count_after_review": assignment.rejection_count
            + (not bool(checker_payload["should_proceed"])),
            "plan": str(plan_payload["plan"]),
            "plan_source": str(plan_payload["source"]),
            "plan_trajectory": list(plan_payload["trajectory"]),
            "checker_output": checker_payload,
        }

    def run_ce(self, assignment: CEAssignment, *, fingerprint: str) -> dict[str, Any]:
        _verify_sif(self.config, assignment, self.capacity)
        identity = checkpoint_identity(
            assignment.case.source, execution_fingerprint=fingerprint
        )
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        plan_path = self.checkpoint_dir / "plan.json"
        existing = _checkpoint(plan_path, identity, "plan")
        plan_payload = {"plan": assignment.accepted_plan, "trajectory": []}
        if existing is None:
            _save_checkpoint(plan_path, identity, "plan", plan_payload)
        elif existing.get("plan") != assignment.accepted_plan:
            raise FatalError("PCCE CE accepted-plan checkpoint mismatch")
        result = PolyBenchPCERunner(
            self.config.pce,
            self.capacity,
            checkpoint_dir=self.checkpoint_dir,
            checkpoint_identity=identity,
            attempt_dir=self.attempt_dir,
        ).run(assignment.case.source)
        result["pcce_status"] = "completed"
        result["accepted_review_relpath"] = str(
            assignment.accepted_review_path.relative_to(self.config.run_dir)
        )
        return result


def cleanup_attempt_workspaces(path: Path) -> None:
    workspaces = path / "workspaces"
    if workspaces.exists():
        shutil.rmtree(workspaces)
