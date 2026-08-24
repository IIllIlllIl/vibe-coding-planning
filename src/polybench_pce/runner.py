"""One identity-bound, container-isolated PolyBench PCE execution."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from src.agents import code_agent, plan_agent
from src.config import AgentConfig, Config, EvaluatorConfig, PromptConfig, SystemConfig
from src.environment.apptainer_env import ApptainerEnvironment, ApptainerSifCache
from src.environment.docker_env import DockerCapacityWindow
from src.environment.repository_baseline import restore_repository_to_base
from src.exceptions import FatalError
from src.optimization.audit import AuditedModel, JsonlLogger
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pce.config import PolyBenchPCEConfig
from src.polybench_pce.dataset import file_sha256
from src.polybench_pce.evaluator import evaluate_polybench_apptainer
from src.polybench_pce.models import PolyBenchPCECase


Evaluator = Callable[..., dict[str, Any]]


class PolyBenchPCERunner:
    def __init__(
        self,
        config: PolyBenchPCEConfig,
        capacity_window: DockerCapacityWindow,
        *,
        checkpoint_dir: Path,
        checkpoint_identity: str,
        attempt_dir: Path,
        evaluator: Evaluator = evaluate_polybench_apptainer,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_identity = checkpoint_identity
        self.attempt_dir = attempt_dir
        self.evaluator = evaluator
        self.audit = JsonlLogger(attempt_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(attempt_dir / "usage.jsonl")

    def _agent_config(self, model: Any) -> AgentConfig:
        return AgentConfig(
            max_steps=model.max_steps,
            cost_limit=model.cost_limit,
            timeout=model.timeout,
            temperature=model.temperature,
        )

    def _base_config(self, model: Any) -> Config:
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
                batch_id="polybench_pce",
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
            agent=self._agent_config(model),
            evaluator=EvaluatorConfig(timeout=self.config.evaluator_timeout),
            api_key=__import__("os").environ[model.api_key_env],
        )

    def _checkpoint(self, phase: str) -> dict[str, Any] | None:
        path = self.checkpoint_dir / f"{phase}.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("checkpoint_identity") != self.checkpoint_identity:
            raise FatalError(f"PCE checkpoint identity mismatch: {path}")
        if value.get("phase") != phase or not isinstance(value.get("payload"), dict):
            raise FatalError(f"invalid PCE checkpoint: {path}")
        self.audit.write("pce_phase_resumed", phase=phase, path=str(path))
        return dict(value["payload"])

    def _save_checkpoint(self, phase: str, payload: dict[str, Any]) -> None:
        atomic_json(
            self.checkpoint_dir / f"{phase}.json",
            {
                "schema_version": 1,
                "checkpoint_identity": self.checkpoint_identity,
                "phase": phase,
                "payload": payload,
            },
        )

    def _verify_sif(self, case: PolyBenchPCECase) -> None:
        expected = Path(case.image.sif_path)
        cache_path = ApptainerSifCache(
            self.config.container.sif_cache_dir, self.capacity_window
        ).sif_path(case.image.requested_ref)
        if cache_path != expected:
            raise FatalError(
                f"frozen SIF path differs from runtime cache identity: {cache_path} != {expected}"
            )
        if not expected.is_file():
            raise FatalError(f"frozen SIF is missing: {expected}")
        if expected.stat().st_size != case.image.sif_bytes:
            raise FatalError(f"frozen SIF size mismatch: {expected}")
        if file_sha256(expected) != case.image.sif_sha256:
            raise FatalError(f"frozen SIF hash mismatch: {expected}")

    def _environment(
        self,
        case: PolyBenchPCECase,
        *,
        timeout: int,
        host_workdir: Path | None = None,
    ) -> ApptainerEnvironment:
        return ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=self.config.docker.workdir,
            sif_cache_dir=self.config.container.sif_cache_dir,
            capacity_window=self.capacity_window,
            timeout=timeout,
            writable_tmpfs=self.config.container.writable_tmpfs,
            git_safe_directories=[self.config.docker.workdir],
            host_workdir=host_workdir,
            initialize_host_workdir=host_workdir is not None,
        )

    @staticmethod
    def _cleanup(path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def _best_effort_environment_cleanup(
        self, env: ApptainerEnvironment, *, phase: str
    ) -> None:
        try:
            env.cleanup()
        except Exception as exc:
            self.audit.write(
                "polybench_pce_environment_cleanup_failed",
                phase=phase,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def _best_effort_workspace_cleanup(self, path: Path, *, phase: str) -> None:
        try:
            self._cleanup(path)
        except Exception as exc:
            self.audit.write(
                "polybench_pce_workspace_cleanup_failed",
                phase=phase,
                path=str(path),
                error_type=type(exc).__name__,
                error=str(exc),
            )

    def run(self, case: PolyBenchPCECase) -> dict[str, Any]:
        self._verify_sif(case)
        plan_checkpoint = self._checkpoint("plan")
        if plan_checkpoint is None:
            env = self._environment(case, timeout=self.config.plan.timeout)
            try:
                restore_repository_to_base(
                    env,
                    case.base_commit,
                    phase="plan",
                    evidence_dir=self.attempt_dir / "repository_baselines" / "plan",
                )
                plan, trajectory = plan_agent.run(
                    self._base_config(self.config.plan),
                    case.issue_description,
                    env,
                    model_wrapper=lambda model: AuditedModel(
                        model,
                        self.usage,
                        phase="plan",
                        context={
                            "instance_id": case.instance_id,
                            "mode": "polybench_pce",
                        },
                    ),
                    failure_trajectory_path=self.attempt_dir / "plan_failure.json",
                )
                plan_checkpoint = {"plan": plan, "trajectory": list(trajectory)}
                self._save_checkpoint("plan", plan_checkpoint)
            finally:
                self._best_effort_environment_cleanup(env, phase="plan")

        code_checkpoint = self._checkpoint("code")
        if code_checkpoint is None:
            code_workspace = self.attempt_dir / "workspaces" / "code"
            self._cleanup(code_workspace)
            env = self._environment(
                case,
                timeout=self.config.code.timeout,
                host_workdir=code_workspace,
            )
            try:
                restore_repository_to_base(
                    env,
                    case.base_commit,
                    phase="code",
                    evidence_dir=self.attempt_dir / "repository_baselines" / "code",
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
                raw_patch, trajectory = code_agent.run(
                    code_config,
                    str(plan_checkpoint["plan"]),
                    case.issue_description,
                    env,
                    model_wrapper=lambda model: AuditedModel(
                        model,
                        self.usage,
                        phase="code",
                        context={
                            "instance_id": case.instance_id,
                            "mode": "polybench_pce",
                        },
                    ),
                    failure_trajectory_path=self.attempt_dir / "code_failure.json",
                    phase_timeout_seconds=(
                        self.config.execution.code_phase_timeout_seconds or None
                    ),
                    allow_empty_submission=True,
                )
                raw_patch_path = self.attempt_dir / "raw_code_submission.patch"
                raw_patch_path.write_text(raw_patch, encoding="utf-8")
                submission = {
                    "policy": "agent_owned_staged_submission_v1",
                    "patch_path": str(raw_patch_path),
                    "patch_sha256": hashlib.sha256(raw_patch.encode()).hexdigest(),
                    "empty_submission": not bool(raw_patch.strip()),
                    "host_patch_transformation": False,
                }
                atomic_json(self.attempt_dir / "patch_submission.json", submission)
                code_checkpoint = {
                    "raw_patch": raw_patch,
                    "patch": raw_patch,
                    "patch_submission": submission,
                    "trajectory": list(trajectory),
                }
                self._save_checkpoint("code", code_checkpoint)
            finally:
                self._best_effort_environment_cleanup(env, phase="code")
                self._best_effort_workspace_cleanup(code_workspace, phase="code")

        evaluator_checkpoint = self._checkpoint("evaluate")
        if evaluator_checkpoint is None:
            eval_workspace = self.attempt_dir / "workspaces" / "evaluate"
            self._cleanup(eval_workspace)
            try:
                evaluator_options: dict[str, Any] = {
                    "container": self.config.container,
                    "capacity_window": self.capacity_window,
                    "workdir": self.config.docker.workdir,
                    "phase_workdir": eval_workspace,
                    "repository_baseline_dir": (
                        self.attempt_dir / "repository_baselines" / "evaluate"
                    ),
                    "timeout": self.config.evaluator_timeout,
                    "result_callback": lambda result: self._save_checkpoint(
                        "evaluate", {"evaluator_result": result}
                    ),
                    "cleanup_error_callback": lambda exc: self.audit.write(
                        "polybench_pce_environment_cleanup_failed",
                        phase="evaluate",
                        error_type=type(exc).__name__,
                        error=str(exc),
                    ),
                }
                if self.config.dependency_cache is not None:
                    evaluator_options["dependency_cache"] = self.config.dependency_cache
                evaluator_result = self.evaluator(
                    str(code_checkpoint["patch"]), case, **evaluator_options
                )
            finally:
                self._best_effort_workspace_cleanup(eval_workspace, phase="evaluate")
            evaluator_checkpoint = {"evaluator_result": evaluator_result}
            # The official evaluator callback writes this before its own cleanup.
            # Writing the same atomic payload again also supports test/custom
            # evaluators that do not use the callback.
            self._save_checkpoint("evaluate", evaluator_checkpoint)

        return {
            "pce_status": "completed",
            "terminal_phase": "evaluate",
            "terminal_reason": str(
                evaluator_checkpoint["evaluator_result"].get(
                    "terminal_kind", "completed"
                )
            ),
            "plan": str(plan_checkpoint["plan"]),
            "plan_trajectory": list(plan_checkpoint["trajectory"]),
            "raw_patch": str(
                code_checkpoint.get("raw_patch", code_checkpoint["patch"])
            ),
            "patch": str(code_checkpoint["patch"]),
            "patch_submission": dict(code_checkpoint.get("patch_submission", {})),
            "code_trajectory": list(code_checkpoint["trajectory"]),
            "evaluator_result": dict(evaluator_checkpoint["evaluator_result"]),
            "final_validation_label": None,
        }


def checkpoint_identity(
    case: PolyBenchPCECase,
    *,
    execution_fingerprint: str,
) -> str:
    value = {
        "schema": 1,
        "execution_fingerprint": execution_fingerprint,
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "image_ref": case.image.requested_ref,
        "oci_digest": case.image.oci_digest,
        "sif_sha256": case.image.sif_sha256,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
