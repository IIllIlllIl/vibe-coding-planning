"""Repo-grounded per-instance reviewer for Online GEPA Reflection."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
from typing import Any

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.online_models import OnlineGEPACase
from src.optimization.reflection import save_reflection_trajectory


_ATTRIBUTIONS = {"plan", "code", "evaluator", "infrastructure", "uncertain"}
_REPOSITORY_STATES = {"base", "generated_patch", "counterfactual"}
_CONFIDENCE_LEVELS = {"low", "medium", "high"}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_instance_review(value: Any, *, instance_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Reflection reviewer output must be a JSON object")
    if value.get("instance_id") != instance_id:
        raise ValueError("Reflection reviewer output instance ID mismatch")
    if value.get("attribution") not in _ATTRIBUTIONS:
        raise ValueError("Reflection reviewer output has invalid attribution")
    questions = value.get("attribution_questions")
    if not isinstance(questions, list) or not questions or not all(
        isinstance(question, str) and question.strip() for question in questions
    ):
        raise ValueError("Reflection reviewer output lacks attribution questions")
    actions = value.get("repository_actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("Reflection reviewer output lacks repository actions")
    action_fields = {"purpose", "command_summary", "repository_state", "result"}
    for action in actions:
        if not isinstance(action, dict) or set(action) != action_fields:
            raise ValueError("Reflection reviewer repository action is malformed")
        if action["repository_state"] not in _REPOSITORY_STATES:
            raise ValueError("Reflection reviewer repository state is invalid")
        if not all(
            isinstance(action[field], str) and action[field].strip()
            for field in action_fields
        ):
            raise ValueError("Reflection reviewer repository action is empty")
    experiments = value.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("Reflection reviewer experiments must be a list")
    experiment_fields = {
        "hypothesis",
        "repository_state",
        "method",
        "observation",
        "supports_hypothesis",
    }
    for experiment in experiments:
        if not isinstance(experiment, dict) or set(experiment) != experiment_fields:
            raise ValueError("Reflection reviewer experiment is malformed")
        if experiment["repository_state"] not in _REPOSITORY_STATES:
            raise ValueError("Reflection reviewer experiment state is invalid")
        if not all(
            isinstance(experiment[field], str) and experiment[field].strip()
            for field in ("hypothesis", "repository_state", "method", "observation")
        ) or not isinstance(experiment["supports_hypothesis"], bool):
            raise ValueError("Reflection reviewer experiment result is invalid")
    skipped_reason = value.get("experiment_skipped_reason")
    if not experiments and not (
        isinstance(skipped_reason, str) and skipped_reason.strip()
    ):
        raise ValueError("Reflection reviewer must explain why experiments were skipped")
    if experiments and not isinstance(skipped_reason, str):
        raise ValueError("Reflection reviewer experiment skip reason must be a string")
    if value.get("confidence") not in _CONFIDENCE_LEVELS:
        raise ValueError("Reflection reviewer confidence is invalid")
    if not isinstance(value.get("remaining_uncertainty"), str):
        raise ValueError("Reflection reviewer remaining uncertainty must be a string")
    assessment = value.get("plan_assessment")
    if not isinstance(assessment, dict) or set(assessment) != {
        "navigation",
        "reproduction",
        "patch_strategy",
        "validation",
    }:
        raise ValueError("Reflection reviewer output lacks plan assessment")
    inspected = value.get("evidence_files")
    if not isinstance(inspected, list) or not inspected:
        raise ValueError("Reflection reviewer did not report inspected evidence")
    required = {"task.md", "generated_plan.md", "evaluator_result.json"}
    if not required.issubset({Path(str(item)).name for item in inspected}):
        raise ValueError("Reflection reviewer did not inspect required evidence")
    return dict(value)


def validate_reviewer_exploration(
    messages: list[dict[str, Any]],
    *,
    repository_path: str,
) -> None:
    assistant_text = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "assistant"
    )
    commands = re.findall(r"```bash\s*(.*?)```", assistant_text, flags=re.DOTALL)
    if not any("/evidence" in command for command in commands):
        raise ValueError("Reflection reviewer did not issue an evidence command")
    if not any(repository_path in command for command in commands):
        raise ValueError("Reflection reviewer did not inspect the base repository")


class OnlineInstanceReflectionReviewer:
    """Review one rollout inside its own clean benchmark SIF."""

    def __init__(
        self,
        config: OnlineOptimizationConfig,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")

    def review(
        self,
        *,
        case: OnlineGEPACase,
        rules: str,
        image_name: str,
        workdir: str,
        evidence: dict[str, Any],
        phase_root: Path,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        evidence_dir = phase_root / "evidence"
        workspace = phase_root / "workspace"
        if phase_root.exists():
            shutil.rmtree(phase_root)
        evidence_dir.mkdir(parents=True)
        workspace.mkdir()
        (evidence_dir / "task.md").write_text(
            case.issue_description + "\n", encoding="utf-8"
        )
        _write_json(
            evidence_dir / "repository.json",
            {
                "repo": case.repository.repo,
                "base_commit": case.repository.base_commit,
                "instance_id": case.repository.instance_id,
            },
        )
        (evidence_dir / "current_rules.md").write_text(rules + "\n", encoding="utf-8")
        (evidence_dir / "generated_plan.md").write_text(
            str(evidence["generated_plan"]), encoding="utf-8"
        )
        _write_json(evidence_dir / "plan_trajectory.json", evidence["plan_trajectory"])
        _write_json(evidence_dir / "code_trajectory.json", evidence["code_trajectory"])
        (evidence_dir / "generated.patch").write_text(
            str(evidence["generated_patch"]), encoding="utf-8"
        )
        _write_json(evidence_dir / "evaluator_result.json", evidence["evaluator_result"])
        _write_json(evidence_dir / "rollout_summary.json", evidence["rollout_summary"])

        DefaultAgent, LitellmModel, DockerEnvironment = import_minisweagent()
        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.reflection.model,
                self.config.reflection.api_base,
            ),
            model_kwargs={
                "api_key": os.environ[self.config.reflection.api_key_env],
                "api_base": self.config.reflection.api_base,
                "temperature": self.config.reflection.temperature,
            },
            cost_tracking="ignore_errors",
        )
        model = AuditedModel(
            base_model,
            self.usage,
            phase="reflection_reviewer",
            context={
                "instance_id": case.instance_id,
                "candidate_sha256": text_sha256(rules),
            },
        )
        if self.config.container.runtime == "apptainer":
            env = ApptainerEnvironment(
                image=image_name,
                cwd="/review",
                sif_cache_dir=self.config.container.sif_cache_dir,
                capacity_window=self.capacity_window,
                run_args=["--bind", f"{evidence_dir.resolve()}:/evidence:ro"],
                timeout=self.config.reflection.timeout,
                container_timeout="4h",
                writable_tmpfs=self.config.container.writable_tmpfs,
                network_disabled=True,
                host_workdir=workspace,
                initialize_host_workdir=False,
                git_safe_directories=[workdir],
            )
        else:
            env = DockerEnvironment(
                image=image_name,
                cwd="/review",
                run_args=[
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    f"type=bind,source={evidence_dir.resolve()},target=/evidence,readonly",
                    "--mount",
                    f"type=bind,source={workspace.resolve()},target=/review",
                ],
                timeout=self.config.reflection.timeout,
                container_timeout="4h",
            )
        try:
            agent = build_default_agent(
                DefaultAgent,
                model,
                env,
                system_template=self.config.reflection_reviewer_prompt,
                instance_template=self.config.reflection_reviewer_instance_template,
                step_limit=self.config.reflection.max_steps,
                cost_limit=self.config.reflection.cost_limit,
            )
            try:
                exit_status, exit_message = agent.run(
                    task="Review this rollout for planning-rule evidence.",
                    instance_id=case.instance_id,
                    issue_description=case.issue_description,
                    repository=case.repository.repo,
                    base_commit=case.repository.base_commit,
                    current_rules=rules,
                    evidence_path="/evidence",
                    repository_path=workdir,
                    output_path="/review/instance_review.json",
                )
                raise_for_permanent_provider_error(exit_status, exit_message)
            except Exception as exc:
                save_reflection_trajectory(
                    evidence_dir,
                    agent.messages,
                    mode="online_planning_instance_review",
                    candidate_sha256=text_sha256(rules),
                    instance_ids=[case.instance_id],
                    status="failed",
                    error=exc,
                )
                raise
            trajectory_path = save_reflection_trajectory(
                evidence_dir,
                agent.messages,
                mode="online_planning_instance_review",
                candidate_sha256=text_sha256(rules),
                instance_ids=[case.instance_id],
                status="completed",
                exit_status=exit_status,
                exit_message=exit_message,
            )
            validate_reviewer_exploration(
                list(agent.messages),
                repository_path=workdir,
            )
            result = env.execute("cat /review/instance_review.json")
            if result.get("returncode") != 0:
                raise ValueError("Reflection reviewer did not create its output file")
            review = validate_instance_review(
                json.loads(str(result.get("output", ""))),
                instance_id=case.instance_id,
            )
            _write_json(evidence_dir / "instance_review.json", review)
            self.audit.write(
                "online_reflection_instance_review_completed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                attribution=review["attribution"],
                trajectory_path=str(trajectory_path),
            )
            return review, list(agent.messages)
        finally:
            env.cleanup()
            if workspace.exists():
                shutil.rmtree(workspace)
