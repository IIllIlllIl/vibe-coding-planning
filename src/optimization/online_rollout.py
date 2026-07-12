"""Single-round online Plan-Code-Test rollout for GEPA planning rules."""

from __future__ import annotations

from dataclasses import replace
import shutil
from pathlib import Path
import time
from typing import Any

from src.agents import code_agent, plan_agent
from src.config import (
    AgentConfig,
    Config,
    EvaluatorConfig,
    PromptConfig,
    SystemConfig,
)
from src.data.instance_loader import InstanceLoader
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow, DockerEnvWrapper
from src.evaluator.runtime_evaluator import evaluate_online_patch
from src.evaluator.swe_evaluator import derive_image_name
from src.exceptions import FatalError
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.online_models import OnlineGEPACase, OnlineRolloutOutput


class OnlinePCTRolloutRunner:
    """Run one current single-round PCT rollout for candidate planning rules."""

    def __init__(
        self,
        config: OnlineOptimizationConfig,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")

    def _agent_config(self, model_config: Any) -> AgentConfig:
        return AgentConfig(
            max_steps=model_config.max_steps,
            cost_limit=model_config.cost_limit,
            timeout=model_config.timeout,
            temperature=model_config.temperature,
        )

    def _base_config(self, model_config: Any) -> Config:
        return Config(
            system=SystemConfig(
                n=1,
                optimization_info_level=1,
                model=model_config.model,
                api_base=model_config.api_base,
                dataset=self.config.dataset.dataset,
                dataset_type=self.config.dataset.dataset_type,
                language_filter=self.config.dataset.language_filter,
                instances=[],
                output_dir=str(self.config.run_dir / "rollouts"),
                batch_id="online_gepa",
                skip_completed_rounds=True,
            ),
            prompts=PromptConfig(
                plan_generation_prompt=self.config.plan_prompt,
                plan_instance_template=self.config.plan_instance_template,
                code_generation_prompt=self.config.code_prompt,
                code_instance_template=self.config.code_instance_template,
                nrpv_block=self.config.nrpv_block,
            ),
            docker=self.config.docker,
            agent=self._agent_config(model_config),
            evaluator=EvaluatorConfig(timeout=self.config.evaluator_timeout),
            api_key=__import__("os").environ[model_config.api_key_env],
        )

    def _load_instance_info(self, case: OnlineGEPACase) -> dict[str, Any]:
        loader = InstanceLoader(
            dataset=self.config.dataset.dataset,
            dataset_type=self.config.dataset.dataset_type,
            language_filter=self.config.dataset.language_filter,
        )
        instance_info = loader.load_instance(case.instance_id)
        instance_info.setdefault("instance_id", case.instance_id)
        return instance_info

    def __call__(
        self,
        case: OnlineGEPACase,
        rules: str,
    ) -> OnlineRolloutOutput:
        if self.config.container.runtime not in ("docker", "apptainer"):
            raise ValueError(
                f"unsupported online rollout runtime: {self.config.container.runtime}"
            )

        candidate_sha256 = text_sha256(rules)
        instance_info = self._load_instance_info(case)
        image_name = derive_image_name(instance_info)
        repo_path = instance_info.get("repo_path", "")
        dataset_type = instance_info.get("dataset_type", "")
        is_pro = dataset_type == "pro" or "dockerhub_tag" in instance_info
        workdir = "/app" if is_pro else self.config.docker.workdir

        self.audit.write(
            "online_rollout_started",
            instance_id=case.instance_id,
            candidate_sha256=candidate_sha256,
            image=image_name,
            workdir=workdir,
            plan_agent_receives_candidate_rules=True,
            code_agent_receives_candidate_rules=False,
            evaluator_receives_candidate_rules=False,
            historical_plan_used=False,
            historical_resolved_used=False,
            historical_asi_used=False,
            plan_prompt_has_candidate_rules=(
                "{{planning_rules}}" in self.config.plan_instance_template
            ),
            code_prompt_has_candidate_rules=(
                "{{candidate_rules}}" in self.config.code_instance_template
                or "{{planning_rules}}" in self.config.code_instance_template
                or "{{candidate_rules}}" in self.config.code_prompt
                or "{{planning_rules}}" in self.config.code_prompt
            ),
        )

        try:
            plan_env: DockerEnvWrapper | ApptainerEnvironment
            plan_env = self._start_environment(
                image_name=image_name,
                workdir=workdir,
                repo_path=repo_path,
                instance_info=instance_info,
                phase="plan",
                candidate_sha256=candidate_sha256,
            )
            try:
                plan_config = self._base_config(self.config.plan)
                plan, plan_trajectory = plan_agent.run(
                    plan_config,
                    case.issue_description,
                    plan_env,
                    planning_rules=rules,
                    model_wrapper=lambda model: AuditedModel(
                        model,
                        self.usage,
                        phase="plan",
                        context={
                            "instance_id": case.instance_id,
                            "candidate_sha256": candidate_sha256,
                            "mode": "online_planning",
                        },
                    ),
                )
            finally:
                self._stop_environment(plan_env)

            code_workspace = (
                self._phase_workdir(
                    instance_info["instance_id"],
                    candidate_sha256,
                    "code",
                )
                if self.config.container.runtime == "apptainer"
                else None
            )
            code_env: DockerEnvWrapper | ApptainerEnvironment
            code_env = self._start_environment(
                image_name=image_name,
                workdir=workdir,
                repo_path=repo_path,
                instance_info=instance_info,
                phase="code",
                candidate_sha256=candidate_sha256,
                host_workdir=code_workspace,
            )
            try:
                base_code_config = self._base_config(self.config.code)
                code_config = replace(
                    base_code_config,
                    prompts=replace(
                        base_code_config.prompts,
                        plan_generation_prompt="",
                        plan_instance_template="",
                    ),
                )
                patch, code_trajectory = code_agent.run(
                    code_config,
                    plan,
                    case.issue_description,
                    code_env,
                    model_wrapper=lambda model: AuditedModel(
                        model,
                        self.usage,
                        phase="code",
                        context={
                            "instance_id": case.instance_id,
                            "candidate_sha256": candidate_sha256,
                            "mode": "online_planning",
                        },
                    ),
                )
            finally:
                try:
                    self._stop_environment(code_env)
                finally:
                    if code_workspace is not None:
                        self._remove_phase_workspace(
                            code_workspace,
                            instance_id=case.instance_id,
                            candidate_sha256=candidate_sha256,
                            phase="code",
                        )

            eval_workdir = self._phase_workdir(
                instance_info["instance_id"],
                candidate_sha256,
                "eval",
            )
            eval_log_root = (
                self.config.run_dir
                / "evaluator_logs"
                / candidate_sha256[:12]
                / case.instance_id
            )
            self.audit.write(
                "online_evaluator_started",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                backend=self.config.evaluator.backend,
                container_runtime=self.config.container.runtime,
                receives_candidate_rules=False,
                receives_plan_trajectory=False,
                receives_code_trajectory=False,
                receives_patch=True,
            )
            try:
                evaluator_result = evaluate_online_patch(
                    patch,
                    instance_info,
                    config=self.config,
                    capacity_window=self.capacity_window,
                    phase_workdir=eval_workdir,
                    persistent_log_root=eval_log_root,
                    run_id_suffix="_online_gepa",
                )
            finally:
                self._remove_phase_workspace(
                    eval_workdir,
                    instance_id=case.instance_id,
                    candidate_sha256=candidate_sha256,
                    phase="eval",
                )
        except Exception as exc:
            self.audit.write(
                "online_rollout_failed",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

        resolved = bool(evaluator_result.get("resolved", False))
        self.audit.write(
            "online_rollout_completed",
            instance_id=case.instance_id,
            candidate_sha256=candidate_sha256,
            resolved=resolved,
            score=float(resolved),
            plan_chars=len(plan),
            patch_chars=len(patch),
            plan_trajectory_messages=len(plan_trajectory),
            code_trajectory_messages=len(code_trajectory),
            evaluator_result_keys=sorted(evaluator_result),
        )
        return OnlineRolloutOutput(
            resolved=resolved,
            plan=plan,
            patch=patch,
            plan_trajectory=tuple(plan_trajectory),
            code_trajectory=tuple(code_trajectory),
            evaluator_result=evaluator_result,
            attribution_hint={
                "candidate_rules_visible_to_plan_agent": True,
                "candidate_rules_visible_to_code_agent": False,
            },
        )

    def _start_environment(
        self,
        *,
        image_name: str,
        workdir: str,
        repo_path: str,
        instance_info: dict[str, Any],
        phase: str,
        candidate_sha256: str,
        host_workdir: Path | None = None,
    ) -> DockerEnvWrapper | ApptainerEnvironment:
        if self.config.container.runtime == "apptainer":
            run_args = []
            if host_workdir is None and repo_path:
                run_args.extend(["--bind", f"{repo_path}:{workdir}"])
            return ApptainerEnvironment(
                image=image_name,
                cwd=workdir,
                sif_cache_dir=self.config.container.sif_cache_dir,
                capacity_window=self.capacity_window,
                run_args=run_args,
                timeout=self.config.plan.timeout,
                writable_tmpfs=self.config.container.writable_tmpfs,
                git_safe_directories=[workdir],
                host_workdir=host_workdir,
                initialize_host_workdir=host_workdir is not None,
            )
        env = DockerEnvWrapper(self.config.docker, self.capacity_window)
        env.start(
            image=image_name,
            workdir=workdir,
            mount_source=repo_path,
            timeout=self.config.plan.timeout,
            instance_info=instance_info,
        )
        return env

    def _phase_workdir(
        self,
        instance_id: str,
        candidate_sha256: str,
        phase: str,
    ) -> Path:
        path = (
            self.config.run_dir
            / "phase_workspaces"
            / candidate_sha256[:12]
            / instance_id
            / phase
        )
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        self.audit.write(
            "online_phase_workspace_prepared",
            instance_id=instance_id,
            candidate_sha256=candidate_sha256,
            phase=phase,
            path=str(path),
        )
        return path

    def _remove_phase_workspace(
        self,
        path: Path,
        *,
        instance_id: str,
        candidate_sha256: str,
        phase: str,
        max_attempts: int = 3,
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            try:
                shutil.rmtree(path)
                self.audit.write(
                    "online_phase_workspace_removed",
                    instance_id=instance_id,
                    candidate_sha256=candidate_sha256,
                    phase=phase,
                    path=str(path),
                    cleanup_attempts=attempt,
                )
                return
            except FileNotFoundError:
                self.audit.write(
                    "online_phase_workspace_removed",
                    instance_id=instance_id,
                    candidate_sha256=candidate_sha256,
                    phase=phase,
                    path=str(path),
                    cleanup_attempts=attempt,
                    already_absent=True,
                )
                return
            except OSError as exc:
                if attempt < max_attempts:
                    time.sleep(2 ** (attempt - 1))
                    continue
                self.audit.write(
                    "online_phase_workspace_cleanup_failed",
                    instance_id=instance_id,
                    candidate_sha256=candidate_sha256,
                    phase=phase,
                    path=str(path),
                    cleanup_attempts=attempt,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise FatalError(
                    f"Failed to remove {phase} phase workspace after "
                    f"{max_attempts} attempts: {path}: {exc}"
                ) from exc

    def _stop_environment(
        self,
        env: DockerEnvWrapper | ApptainerEnvironment,
    ) -> None:
        if isinstance(env, ApptainerEnvironment):
            env.cleanup()
        else:
            env.stop()
