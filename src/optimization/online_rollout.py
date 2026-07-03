"""Single-round online Plan-Code-Test rollout for GEPA planning rules."""

from __future__ import annotations

from dataclasses import replace
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
from src.environment.docker_env import DockerCapacityWindow, DockerEnvWrapper
from src.evaluator.swe_evaluator import derive_image_name, evaluate
from src.optimization.audit import JsonlLogger, text_sha256
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
        if self.config.container.runtime != "docker":
            raise NotImplementedError(
                "online PCT rollout currently supports local Docker only; "
                "full Apptainer PCT/PCC evaluator support is still a separate design item"
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
            env = DockerEnvWrapper(self.config.docker, self.capacity_window)
            try:
                env.start(
                    image=image_name,
                    workdir=workdir,
                    mount_source=repo_path,
                    timeout=self.config.plan.timeout,
                    instance_info=instance_info,
                )
                plan_config = self._base_config(self.config.plan)
                plan, plan_trajectory = plan_agent.run(
                    plan_config,
                    case.issue_description,
                    env,
                    planning_rules=rules,
                )
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
                    env,
                )
            finally:
                env.stop()

            evaluator_result = evaluate(
                patch,
                instance_info,
                timeout=self.config.evaluator_timeout,
                run_id_suffix="_online_gepa",
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
