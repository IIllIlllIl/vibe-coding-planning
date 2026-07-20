"""Repo-grounded per-instance reviewer for Online GEPA Reflection."""

from __future__ import annotations

import json
import os
from pathlib import Path
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


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_instance_review(
    value: Any,
    *,
    instance_id: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Reflection reviewer output must be a JSON object")
    if value.get("instance_id") != instance_id:
        raise ValueError("Reflection reviewer output instance ID mismatch")
    review_status = value.get("review_status")
    if isinstance(review_status, str) and review_status != "completed":
        return dict(value)
    assessment = value.get("plan_assessment")
    if not isinstance(assessment, dict) or set(assessment) != {
        "correct",
        "missing_or_wrong",
        "repository_findings",
    }:
        raise ValueError("Reflection reviewer output lacks plan assessment")
    required_text = (
        *assessment.values(),
        value.get("code_plan_alignment"),
        value.get("outcome_attribution"),
        value.get("planning_lesson"),
        value.get("uncertainty"),
    )
    if not all(isinstance(item, str) for item in required_text):
        raise ValueError("Reflection reviewer analysis fields must be strings")
    return dict(value)


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
                step_limit=None,
                cost_limit=None,
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
            persisted_trajectory = json.loads(
                trajectory_path.read_text(encoding="utf-8")
            )["messages"]
            result = env.execute("cat /review/instance_review.json")
            if result.get("returncode") != 0:
                raise ValueError("Reflection reviewer did not create its output file")
            review = validate_instance_review(
                json.loads(str(result.get("output", ""))),
                instance_id=case.instance_id,
            )
            review["review_status"] = "completed"
            _write_json(evidence_dir / "instance_review.json", review)
            self.audit.write(
                "online_reflection_instance_review_completed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                trajectory_path=str(trajectory_path),
            )
            return review, list(persisted_trajectory)
        finally:
            env.cleanup()
            if workspace.exists():
                shutil.rmtree(workspace)
