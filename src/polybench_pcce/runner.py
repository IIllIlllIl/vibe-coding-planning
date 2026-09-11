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
from src.optimization.checker import CheckerOutputContractError, DockerChecker
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.models import CheckerOutput, RepositoryEvidence
from src.optimization.playbook import RejectPlaybook, validate_checker_result
from src.optimization.playbook_runtime import (
    PlaybookAgentOutputContractError,
    PromptModel,
    _render,
)
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
        "active_concerns": list(assignment.active_concerns),
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


def validate_dialogue_checker_output(
    value: dict[str, Any], active_concerns: tuple[dict[str, Any], ...]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"concern_results"}:
        raise ValueError("Dialogue Checker output must contain only concern_results")
    rows = value["concern_results"]
    if not isinstance(rows, list) or len(rows) != len(active_concerns):
        raise ValueError("Dialogue Checker must return one result per active concern")
    normalized = []
    for concern, row in zip(active_concerns, rows, strict=True):
        if not isinstance(row, dict) or set(row) != {
            "rule_number",
            "status",
            "reason",
        }:
            raise ValueError("Dialogue Checker concern result has an invalid schema")
        if row["rule_number"] != concern["rule_number"]:
            raise ValueError("Dialogue Checker concern identity mismatch")
        if row["status"] not in {
            "cleared",
            "needs_clarification",
            "still_blocking",
        }:
            raise ValueError("Dialogue Checker concern status is invalid")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("Dialogue Checker reason must be non-empty")
        normalized.append(
            {
                "rule_number": row["rule_number"],
                "status": row["status"],
                "reason": row["reason"].strip(),
            }
        )
    return {"concern_results": normalized}


def _validate_planner_response(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict) or set(parsed) != {
            "developer_response",
            "revised_plan",
        }:
            raise ValueError(
                "output must contain developer_response and revised_plan"
            )
        if not isinstance(parsed["developer_response"], str) or not parsed[
            "developer_response"
        ].strip():
            raise ValueError("developer_response must be non-empty")
        if not isinstance(parsed["revised_plan"], str) or not parsed[
            "revised_plan"
        ].strip():
            raise ValueError("revised_plan must be non-empty")
    except (ValueError, json.JSONDecodeError) as exc:
        raise CheckerOutputContractError(
            f"ACE Planner output contract invalid: {exc}"
        ) from exc
    return {
        "developer_response": parsed["developer_response"].strip(),
        "revised_plan": parsed["revised_plan"].strip(),
    }


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
                plan_instance_template=(
                    "{{task}}"
                    if self.config.execution_mode == "ace_pcce"
                    else self.config.plan_revision_instance_template
                ),
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

    def _environment(
        self,
        assignment: PCReviewAssignment,
        *,
        host_workdir: Path,
    ) -> ApptainerEnvironment:
        case = assignment.case.source
        return ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=self.config.pce.docker.workdir,
            sif_cache_dir=self.config.pce.container.sif_cache_dir,
            capacity_window=self.capacity,
            run_args=["--containall"],
            timeout=self.config.pce.plan.timeout,
            writable_tmpfs=self.config.pce.container.writable_tmpfs,
            git_safe_directories=[self.config.pce.docker.workdir],
            host_workdir=host_workdir,
            initialize_host_workdir=True,
            isolate_tmp=True,
            block_git_remote_operations=True,
        )

    def _cleanup_workspace(self, path: Path, *, phase: str) -> None:
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception as exc:
            self.audit.write(
                "pcce_workspace_cleanup_failed",
                phase=phase,
                path=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def _prompt_model_config(self) -> dict[str, Any]:
        model = self.config.checker.checker
        return {
            "model": model.model,
            "api_base": model.api_base,
            "api_key_env": model.api_key_env,
            "temperature": 0.0,
        }

    def _run_ace_initial_checker(
        self,
        assignment: PCReviewAssignment,
        *,
        identity: str,
        playbook: RejectPlaybook,
    ) -> dict[str, Any]:
        checker_path = self.checkpoint_dir / "checker.json"
        existing = _checkpoint(checker_path, identity, "checker")
        if existing is not None:
            return existing
        visible = playbook.render_for_checker()
        user = _render(
            self.config.checker_instance_template,
            issue=assignment.case.source.issue_description,
            plan=assignment.input_plan,
            checker_visible_playbook=visible,
            retry_feedback=assignment.retry_feedback,
        )
        try:
            raw, trajectory = PromptModel(self._prompt_model_config())(
                self.config.checker_prompt, user
            )
            parsed = validate_checker_result(raw, playbook, trajectory=trajectory)
        except (PlaybookAgentOutputContractError, ValueError) as exc:
            raise CheckerOutputContractError(
                f"ACE initial Checker output contract invalid: {exc}"
            ) from exc
        triggered = [
            {
                "rule_number": result.rule_number,
                "rule_text": playbook.bullets[result.rule_number - 1].text,
                "plan_evidence": list(result.plan_evidence),
                "reason": result.reason,
            }
            for result in parsed.rule_results
            if result.triggered
        ]
        payload = {
            "stage": "initial_playbook_checker",
            "should_proceed": not triggered,
            "rule_results": [result.to_dict() for result in parsed.rule_results],
            "triggered_rules": triggered,
            "revision_feedback": "",
            "trajectory": trajectory,
        }
        _save_checkpoint(checker_path, identity, "checker", payload)
        return payload

    def _run_ace_revision(
        self,
        assignment: PCReviewAssignment,
        *,
        identity: str,
    ) -> dict[str, Any]:
        plan_path = self.checkpoint_dir / "plan.json"
        existing = _checkpoint(plan_path, identity, "plan")
        if existing is not None:
            return existing
        revision_task = _render(
            self.config.plan_revision_instance_template,
            issue=assignment.case.source.issue_description,
            current_plan=assignment.input_plan,
            developer_concerns=json.dumps(
                list(assignment.active_concerns), ensure_ascii=False, indent=2
            ),
            previous_dialogue_feedback=assignment.previous_feedback,
            host_validation_feedback=assignment.retry_feedback,
        )
        revision_workspace = self.attempt_dir / "workspaces" / "plan_revision"
        if revision_workspace.exists():
            shutil.rmtree(revision_workspace)
        env = self._environment(assignment, host_workdir=revision_workspace)
        try:
            restore_repository_to_base(
                env,
                assignment.case.source.base_commit,
                phase="plan_revision",
                evidence_dir=self.attempt_dir
                / "repository_baselines"
                / "plan_revision",
                timeout=self.config.pce.execution.repository_command_timeout_seconds,
            )
            response, trajectory = plan_agent.run(
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
                        "mode": "polybench_ace_pcce",
                    },
                ),
                failure_trajectory_path=self.attempt_dir / "plan_failure.json",
            )
            parsed = _validate_planner_response(response)
            payload = {
                "plan": parsed["revised_plan"],
                "developer_response": parsed["developer_response"],
                "trajectory": list(trajectory),
                "source": "ace_planner_dialogue",
            }
            _save_checkpoint(plan_path, identity, "plan", payload)
            return payload
        finally:
            try:
                env.cleanup()
            except Exception as exc:
                self.audit.write(
                    "pcce_cleanup_failed", phase="plan_revision", error=str(exc)
                )
            self._cleanup_workspace(revision_workspace, phase="plan_revision")

    def _run_ace_dialogue_checker(
        self,
        assignment: PCReviewAssignment,
        plan_payload: dict[str, Any],
        *,
        identity: str,
    ) -> dict[str, Any]:
        checker_path = self.checkpoint_dir / "checker.json"
        existing = _checkpoint(checker_path, identity, "checker")
        if existing is not None:
            return existing
        user = _render(
            self.config.dialogue_checker_instance_template,
            issue=assignment.case.source.issue_description,
            previous_plan=assignment.input_plan,
            current_plan=str(plan_payload["plan"]),
            developer_concerns=json.dumps(
                list(assignment.active_concerns), ensure_ascii=False, indent=2
            ),
            planner_response=str(plan_payload["developer_response"]),
            retry_feedback=assignment.retry_feedback,
        )
        try:
            raw, trajectory = PromptModel(self._prompt_model_config())(
                self.config.dialogue_checker_prompt, user
            )
            parsed = validate_dialogue_checker_output(raw, assignment.active_concerns)
        except (PlaybookAgentOutputContractError, ValueError) as exc:
            raise CheckerOutputContractError(
                f"ACE Dialogue Checker output contract invalid: {exc}"
            ) from exc
        unresolved = [
            {
                **concern,
                "dialogue_status": result["status"],
                "dialogue_reason": result["reason"],
            }
            for concern, result in zip(
                assignment.active_concerns,
                parsed["concern_results"],
                strict=True,
            )
            if result["status"] != "cleared"
        ]
        feedback = "\n".join(
            f"Rule {item['rule_number']} [{item['dialogue_status']}]: "
            f"{item['dialogue_reason']}"
            for item in unresolved
        )
        payload = {
            "stage": "dialogue_checker",
            "should_proceed": not unresolved,
            "concern_results": parsed["concern_results"],
            "unresolved_concerns": unresolved,
            "revision_feedback": feedback,
            "trajectory": trajectory,
        }
        _save_checkpoint(checker_path, identity, "checker", payload)
        return payload

    def _run_ace_pc(
        self,
        assignment: PCReviewAssignment,
        *,
        fingerprint: str,
        guideline: str,
    ) -> dict[str, Any]:
        playbook = RejectPlaybook.parse(guideline)
        identity = _review_identity(assignment, fingerprint, playbook.serialize())
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        if assignment.review_index == 1:
            plan_payload = {
                "plan": assignment.input_plan,
                "trajectory": [],
                "source": "frozen_historical_pce",
            }
            checker_payload = self._run_ace_initial_checker(
                assignment, identity=identity, playbook=playbook
            )
        else:
            _verify_sif(self.config, assignment, self.capacity)
            plan_payload = self._run_ace_revision(assignment, identity=identity)
            checker_payload = self._run_ace_dialogue_checker(
                assignment, plan_payload, identity=identity
            )
        return {
            "pc_status": "completed",
            "review_index": assignment.review_index,
            "rejection_count_before_review": assignment.rejection_count,
            "rejection_count_after_review": assignment.rejection_count
            + (not bool(checker_payload["should_proceed"])),
            "plan": str(plan_payload["plan"]),
            "plan_source": str(plan_payload["source"]),
            "plan_trajectory": list(plan_payload["trajectory"]),
            "developer_response": plan_payload.get("developer_response", ""),
            "active_concerns": list(assignment.active_concerns),
            "checker_output": checker_payload,
        }

    def run_pc(
        self,
        assignment: PCReviewAssignment,
        *,
        fingerprint: str,
        guideline: str,
    ) -> dict[str, Any]:
        if self.config.execution_mode == "ace_pcce":
            return self._run_ace_pc(
                assignment, fingerprint=fingerprint, guideline=guideline
            )
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
                revision_workspace = (
                    self.attempt_dir / "workspaces" / "plan_revision"
                )
                if revision_workspace.exists():
                    shutil.rmtree(revision_workspace)
                env = self._environment(
                    assignment,
                    host_workdir=revision_workspace,
                )
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
                        timeout=(
                            self.config.pce.execution.repository_command_timeout_seconds
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
                    self._cleanup_workspace(
                        revision_workspace, phase="plan_revision"
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

            checker_workspace = self.attempt_dir / "workspaces" / "checker"
            if checker_workspace.exists():
                shutil.rmtree(checker_workspace)
            try:
                DockerChecker(checker_config, self.capacity)(
                    PCCECheckerCase(assignment.case.source, str(plan_payload["plan"]), {}),  # type: ignore[arg-type]
                    guideline,
                    trajectory_journal_path=(
                        self.attempt_dir / "checker_trajectory.jsonl"
                    ),
                    output_validator=validate_pcce_checker_output,
                    completion_callback=save_completed_checker,
                    repository_baseline_dir=(
                        self.attempt_dir / "repository_baselines" / "checker"
                    ),
                    apptainer_host_workdir=checker_workspace,
                    repository_initializer=lambda env, evidence_dir: (
                        restore_repository_to_base(
                            env,
                            assignment.case.source.base_commit,
                            phase="checker",
                            evidence_dir=evidence_dir,
                            timeout=(
                                self.config.pce.execution.repository_command_timeout_seconds
                            ),
                        )
                    ),
                    apptainer_run_args=["--containall"],
                    apptainer_isolate_tmp=True,
                )
            finally:
                self._cleanup_workspace(checker_workspace, phase="checker")
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
