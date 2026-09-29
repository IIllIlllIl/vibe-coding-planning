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
from src.environment.repository_history import (
    RepositoryHistoryCache,
    install_repository_history_bundle,
)
from src.environment.source_access import extract_http_urls
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
        self.source_access_path = attempt_dir / "source_access.jsonl"

    def _agent_config(self, model: Any) -> AgentConfig:
        return AgentConfig(
            max_steps=model.max_steps,
            cost_limit=model.cost_limit,
            timeout=model.timeout,
            temperature=model.temperature,
            thinking=model.thinking,
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
        phase: str = "legacy",
        host_workdir: Path | None = None,
    ) -> ApptainerEnvironment:
        safe_boundary = (
            getattr(self.config, "plan_submission_protocol", "legacy_stdout_v1")
            == "direct_human_markdown_v5"
        )
        return ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=self.config.docker.workdir,
            sif_cache_dir=self.config.container.sif_cache_dir,
            capacity_window=self.capacity_window,
            timeout=timeout,
            writable_tmpfs=self.config.container.writable_tmpfs,
            run_args=(
                ["--containall", "--no-mount", "cwd"]
                if safe_boundary
                else ["--containall"]
            ),
            git_safe_directories=[self.config.docker.workdir],
            host_workdir=host_workdir,
            initialize_host_workdir=host_workdir is not None,
            isolate_tmp=True,
            network_disabled=getattr(self.config, "agent_network_disabled", False),
            masked_container_paths=(
                ["/opt/miniconda3/pkgs"] if safe_boundary else None
            ),
            source_access_prompt_urls=(
                list(extract_http_urls(case.issue_description))
                if safe_boundary
                else None
            ),
            source_access_log_path=(
                self.source_access_path if safe_boundary else None
            ),
            source_access_context=(
                {"instance_id": case.instance_id, "phase": phase}
                if safe_boundary
                else None
            ),
        )

    def _prepared_history(self, case: PolyBenchPCECase) -> Path:
        cache = RepositoryHistoryCache(
            self.config.container.sif_cache_dir.parent
            / "repository-history-cache-v1"
        )
        existing = cache.validate(
            sif_sha256=case.image.sif_sha256,
            base_commit=case.base_commit,
        )
        if existing is None:
            raise FatalError(
                f"prepared base-ancestor Git history is missing for {case.instance_id}"
            )
        bundle, manifest = existing
        atomic_json(
            self.attempt_dir / "repository_history_artifact.json",
            {**manifest, "bundle": str(bundle)},
        )
        return bundle

    def _restore_agent_repository(
        self,
        env: ApptainerEnvironment,
        case: PolyBenchPCECase,
        *,
        phase: str,
        host_workdir: Path,
        history_bundle: Path | None,
    ) -> None:
        evidence_dir = self.attempt_dir / "repository_baselines" / phase
        if history_bundle is not None:
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence = install_repository_history_bundle(
                repository_dir=host_workdir,
                bundle=history_bundle,
                base_commit=case.base_commit,
            )
            atomic_json(evidence_dir / "repository_history_install.json", evidence)
        restore_repository_to_base(
            env,
            case.base_commit,
            phase=phase,
            evidence_dir=evidence_dir,
            timeout=self.config.execution.repository_command_timeout_seconds,
            prune_future_history=False,
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

    def _capture_code_workspace_evidence(
        self,
        env: ApptainerEnvironment,
        *,
        submitted_patch: str,
    ) -> dict[str, Any]:
        """Freeze the Agent-owned implementation/diagnostic split."""

        commands = {
            "staged_patch": "git diff --cached --binary --full-index",
            "staged_paths": "git diff --cached --name-only",
            "unstaged_patch": "git diff --binary --full-index",
            "untracked_paths": "git ls-files --others --exclude-standard",
            "status": "git status --porcelain=v1 --untracked-files=all",
        }
        observations: dict[str, dict[str, Any]] = {}
        for name, command in commands.items():
            observation = dict(
                env.execute(
                    command,
                    timeout=self.config.execution.repository_command_timeout_seconds,
                )
            )
            observations[name] = {
                "command": command,
                "returncode": observation.get("returncode"),
                "output": str(observation.get("output", "")),
            }
            if observation.get("returncode") != 0:
                raise RuntimeError(
                    f"could not capture Code workspace {name}: "
                    f"{str(observation.get('output', ''))[:500]}"
                )

        unstaged_patch = observations["unstaged_patch"]["output"]
        unstaged_path = self.attempt_dir / "unstaged_diagnostic_changes.patch"
        unstaged_path.write_text(unstaged_patch, encoding="utf-8")
        staged_patch = observations["staged_patch"]["output"]
        evidence = {
            "schema_version": 1,
            "policy": "agent_classified_staged_implementation_v1",
            "implementation_submission": {
                "staged_paths": observations["staged_paths"]["output"].splitlines(),
                "observed_patch_sha256": hashlib.sha256(
                    staged_patch.encode()
                ).hexdigest(),
                "agent_submission_sha256": hashlib.sha256(
                    submitted_patch.encode()
                ).hexdigest(),
                "comparison": "terminal_newlines_ignored",
                "matches_agent_submission": (
                    staged_patch.rstrip("\n") == submitted_patch.rstrip("\n")
                ),
            },
            "diagnostic_changes": {
                "unstaged_patch_path": str(unstaged_path),
                "unstaged_patch_sha256": hashlib.sha256(
                    unstaged_patch.encode()
                ).hexdigest(),
                "unstaged_patch_chars": len(unstaged_patch),
                "untracked_paths": observations["untracked_paths"][
                    "output"
                ].splitlines(),
            },
            "repository_status": observations["status"],
        }
        atomic_json(self.attempt_dir / "code_workspace_evidence.json", evidence)
        return evidence

    def run_plan(
        self,
        case: PolyBenchPCECase,
        *,
        _sif_verified: bool = False,
        _prepared_history_bundle: Path | None = None,
    ) -> dict[str, Any]:
        """Produce or resume the exact PCE Planner artifact without running CE."""
        if not _sif_verified:
            self._verify_sif(case)
        plan_checkpoint = self._checkpoint("plan")
        safe_boundary = self.config.plan_submission_protocol == "direct_human_markdown_v5"
        history_bundle = _prepared_history_bundle
        if safe_boundary and plan_checkpoint is None and history_bundle is None:
            history_bundle = self._prepared_history(case)
        if plan_checkpoint is None:
            plan_workspace = self.attempt_dir / "workspaces" / "plan"
            self._cleanup(plan_workspace)
            env = self._environment(
                case,
                timeout=self.config.plan.timeout,
                phase="plan",
                host_workdir=plan_workspace,
            )
            try:
                self._restore_agent_repository(
                    env,
                    case,
                    phase="plan",
                    host_workdir=plan_workspace,
                    history_bundle=history_bundle,
                )
                submission_protocol = self.config.plan_submission_protocol
                direct_submission = (
                    submission_protocol
                    == plan_agent.DIRECT_HUMAN_BOUNDED_MARKDOWN_PROTOCOL
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
                    **(
                        {
                            "require_direct_submission": True,
                            "direct_submission_protocol": submission_protocol,
                        }
                        if direct_submission
                        else {}
                    ),
                )
                plan_checkpoint = {"plan": plan, "trajectory": list(trajectory)}
                if direct_submission:
                    raw_submission = plan_agent.direct_plan_terminal_response(
                        trajectory,
                        start_marker=plan_agent.DIRECT_HUMAN_PLAN_MARKER,
                    )
                    if raw_submission is None:
                        raise FatalError(
                            "bounded Plan submission is absent from its trajectory"
                        )
                    plan_checkpoint.update(
                        {
                            "plan_sha256": hashlib.sha256(plan.encode()).hexdigest(),
                            "submission_protocol": submission_protocol,
                            "raw_plan_submission": raw_submission,
                            "raw_plan_submission_sha256": hashlib.sha256(
                                raw_submission.encode()
                            ).hexdigest(),
                            "plan_boundary": {
                                "start_marker": plan_agent.DIRECT_HUMAN_PLAN_MARKER,
                                "end_marker": plan_agent.DIRECT_HUMAN_PLAN_END_MARKER,
                            },
                        }
                    )
                self._save_checkpoint("plan", plan_checkpoint)
            finally:
                self._best_effort_environment_cleanup(env, phase="plan")
                self._best_effort_workspace_cleanup(plan_workspace, phase="plan")

        if self.config.plan_submission_protocol == "direct_human_markdown_v5":
            if (
                plan_checkpoint.get("submission_protocol")
                != self.config.plan_submission_protocol
            ):
                raise FatalError("PCE Plan checkpoint submission protocol mismatch")
            if (
                plan_checkpoint.get("plan_sha256")
                != hashlib.sha256(str(plan_checkpoint["plan"]).encode()).hexdigest()
            ):
                raise FatalError("PCE Plan checkpoint hash mismatch")
            raw_submission = plan_checkpoint.get("raw_plan_submission")
            if (
                not isinstance(raw_submission, str)
                or plan_checkpoint.get("raw_plan_submission_sha256")
                != hashlib.sha256(raw_submission.encode()).hexdigest()
            ):
                raise FatalError("PCE raw Plan submission checkpoint hash mismatch")

        return plan_checkpoint

    def run(self, case: PolyBenchPCECase) -> dict[str, Any]:
        self._verify_sif(case)
        prior_plan = self._checkpoint("plan")
        code_checkpoint = self._checkpoint("code")
        history_bundle = (
            self._prepared_history(case)
            if self.config.plan_submission_protocol == "direct_human_markdown_v5"
            and (prior_plan is None or code_checkpoint is None)
            else None
        )
        plan_checkpoint = self.run_plan(
            case,
            _sif_verified=True,
            _prepared_history_bundle=history_bundle,
        )

        if code_checkpoint is None:
            code_workspace = self.attempt_dir / "workspaces" / "code"
            self._cleanup(code_workspace)
            env = self._environment(
                case,
                timeout=self.config.code.timeout,
                phase="code",
                host_workdir=code_workspace,
            )
            try:
                self._restore_agent_repository(
                    env,
                    case,
                    phase="code",
                    host_workdir=code_workspace,
                    history_bundle=history_bundle,
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
                workspace_evidence = self._capture_code_workspace_evidence(
                    env,
                    submitted_patch=raw_patch,
                )
                submission = {
                    "policy": "agent_classified_staged_implementation_v1",
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
                    "workspace_evidence": workspace_evidence,
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
                    "repository_command_timeout": (
                        self.config.execution.repository_command_timeout_seconds
                    ),
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
                if self.config.evaluator_network_disabled:
                    evaluator_options["network_disabled_override"] = True
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
            "code_workspace_evidence": dict(
                code_checkpoint.get("workspace_evidence", {})
            ),
            "code_trajectory": list(code_checkpoint["trajectory"]),
            "evaluator_result": dict(evaluator_checkpoint["evaluator_result"]),
            "final_validation_label": None,
        }


def checkpoint_identity(
    case: PolyBenchPCECase,
    *,
    execution_fingerprint: str,
    repetition: int = 1,
) -> str:
    if repetition < 1:
        raise ValueError("repetition must be positive")
    value = {
        "schema": 1,
        "execution_fingerprint": execution_fingerprint,
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "image_ref": case.image.requested_ref,
        "oci_digest": case.image.oci_digest,
        "sif_sha256": case.image.sif_sha256,
    }
    if repetition != 1:
        value["repetition"] = repetition
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
