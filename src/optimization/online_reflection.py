"""Reflection proposer for online GEPA planning rules."""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Mapping, Sequence

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    extract_last_assistant,
    import_minisweagent,
)
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.reflection import (
    EvidenceBundleWriter,
    save_reflection_trajectory,
)


class OnlinePlanningReflectionProposer:
    def __init__(
        self,
        config: OnlineOptimizationConfig,
        capacity_window: DockerCapacityWindow,
        *,
        successful_proposals: int = 0,
        failures: Sequence[Mapping[str, str]] = (),
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.bundles = EvidenceBundleWriter(config.run_dir, mode="online_planning")
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self.errors = JsonlLogger(config.run_dir / "errors.jsonl")
        self.failures = [dict(failure) for failure in failures]
        self.successful_proposals = successful_proposals

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        try:
            proposal = self._propose(
                candidate,
                reflective_dataset,
                components_to_update,
            )
        except Exception as exc:
            failure = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "candidate_sha256": text_sha256(candidate.get("rules", "")),
            }
            self.failures.append(failure)
            self.audit.write("online_reflection_failed", **failure)
            self.errors.write("online_reflection_failed", **failure)
            raise
        self.successful_proposals += 1
        return proposal

    def _propose(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("GEPA may only update the rules component")
        records = list(reflective_dataset["rules"])
        bundle = self.bundles.write(records)
        instance_ids = [str(record["instance_id"]) for record in records]
        parent_sha256 = text_sha256(candidate["rules"])
        self.audit.write(
            "online_reflection_bundle_created",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            instance_ids=instance_ids,
            minibatch_size=len(instance_ids),
            current_minibatch_only=True,
            contains_generated_plan=True,
            contains_plan_trajectory=True,
            contains_code_trajectory=True,
            contains_generated_patch=True,
            contains_evaluator_result=True,
            contains_current_rollout_resolved=True,
            contains_historical_plan=False,
            contains_historical_resolved=False,
            contains_historical_asi=False,
        )
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
            phase="reflection",
            context={
                "candidate_sha256": parent_sha256,
                "instance_ids": instance_ids,
                "bundle_path": str(bundle),
                "mode": "online_planning",
            },
        )
        if self.config.container.runtime == "apptainer":
            run_args = [
                "--bind",
                f"{bundle.resolve()}:/evidence:ro",
            ]
            workspace = (
                self.config.run_dir
                / "online_reflection_workspaces"
                / parent_sha256[:12]
                / bundle.name
            )
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            run_args = [
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=bind,source={bundle.resolve()},target=/evidence,readonly",
            ]
            workspace = None
        self.audit.write(
            "online_reflection_mount_configured",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            container_path="/evidence",
            readonly=True,
            network_disabled=True,
            runtime=self.config.container.runtime,
            host_workdir=str(workspace) if workspace is not None else None,
        )
        with self.capacity_window.lease():
            if self.config.container.runtime == "apptainer":
                env = ApptainerEnvironment(
                    image="python:3.12-slim",
                    cwd="/tmp",
                    sif_cache_dir=self.config.container.sif_cache_dir,
                    capacity_window=self.capacity_window,
                    run_args=run_args,
                    timeout=self.config.reflection.timeout,
                    container_timeout="4h",
                    writable_tmpfs=self.config.container.writable_tmpfs,
                    network_disabled=True,
                    host_workdir=workspace,
                    initialize_host_workdir=False,
                )
            else:
                env = DockerEnvironment(
                    image="python:3.12-slim",
                    cwd="/evidence",
                    run_args=run_args,
                    timeout=self.config.reflection.timeout,
                    container_timeout="4h",
                )
            try:
                agent = build_default_agent(
                    DefaultAgent,
                    model,
                    env,
                    system_template=self.config.reflection_prompt,
                    instance_template=self.config.reflection_instance_template,
                    step_limit=self.config.reflection.max_steps,
                    cost_limit=self.config.reflection.cost_limit,
                )
                try:
                    exit_status, exit_message = agent.run(
                        task=(
                            "Review the current online rollout evidence and improve "
                            "the complete planning checklist."
                        ),
                        current_rules=candidate["rules"],
                        evidence_path="/evidence",
                    )
                except Exception as exc:
                    save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="online_planning",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="failed",
                        error=exc,
                    )
                    raise
                trajectory_path = save_reflection_trajectory(
                    bundle,
                    agent.messages,
                    mode="online_planning",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    status="completed",
                    exit_status=exit_status,
                    exit_message=exit_message,
                )
                final_message = extract_last_assistant(agent.messages)
                result = env.execute("cat /tmp/candidate_rules.txt")
                candidate_file_found = result.get("returncode") == 0
                self.audit.write(
                    "online_reflection_agent_completed",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    trajectory_path=str(trajectory_path),
                    exit_status=exit_status,
                    exit_message=exit_message,
                    trajectory_messages=len(agent.messages),
                    model_calls=int(getattr(model, "n_calls", 0)),
                    candidate_file_found=candidate_file_found,
                    candidate_file_chars=(
                        len(str(result.get("output", "")))
                        if candidate_file_found
                        else 0
                    ),
                    final_assistant_chars=len(final_message),
                )
                text = result.get("output", "") if candidate_file_found else ""
                if not text.strip():
                    match = re.search(
                        r"```(?:text)?\s*(.*?)```",
                        final_message,
                        re.DOTALL,
                    )
                    text = match.group(1) if match else ""
                if not text.strip():
                    raise ValueError("reflection agent produced empty candidate rules")
                proposed = text.strip()
                looks_like_patch = "diff --git " in proposed
                self.audit.write(
                    "online_reflection_candidate_proposed",
                    parent_candidate_sha256=parent_sha256,
                    proposed_candidate_sha256=text_sha256(proposed),
                    proposed_rules_empty=proposed == "",
                    output_is_complete_replacement=not looks_like_patch,
                    looks_like_git_patch=looks_like_patch,
                    output_length_chars=len(proposed),
                    instance_ids=instance_ids,
                )
                if looks_like_patch:
                    raise ValueError(
                        "reflection agent produced a patch instead of rules"
                    )
                return {"rules": proposed}
            finally:
                env.cleanup()
