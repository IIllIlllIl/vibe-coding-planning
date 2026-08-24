"""Fixed binary Checker Agent used by GEPA."""

from __future__ import annotations

import json
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import threading
from typing import Any, Callable, Protocol

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    import_minisweagent,
)
from src.environment.apptainer_env import (
    ApptainerEnvironment,
    ApptainerSifCache,
)
from src.environment.docker_env import (
    DockerCapacityWindow,
    DockerEnvWrapper,
    ensure_project_image_local,
)
from src.environment.repository_baseline import restore_repository_to_base
from src.evaluator.swe_evaluator import derive_image_name
from src.optimization.audit import (
    AuditedModel,
    JsonlLogger,
    redact_sensitive,
    text_sha256,
)
from src.optimization.config import OptimizationConfig
from src.optimization.models import (
    CheckerOutput,
    GEPACase,
    RepositoryEvidence,
)


class CheckerRunner(Protocol):
    def __call__(
        self,
        case: GEPACase,
        rules: str,
        *,
        retry_feedback: str = "",
        trajectory_journal_path: Path | None = None,
        output_validator: Callable[[dict[str, Any]], CheckerOutput] = ...,
        completion_callback: Callable[[CheckerOutput], None] | None = None,
        repository_baseline_dir: Path | None = None,
    ) -> CheckerOutput: ...


class CheckerOutputContractError(ValueError):
    """The Checker submitted output that the host could not validate."""


class CheckerAgentTimeout(BaseException):
    """A deadline signal that nested model libraries must not swallow."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Checker Agent exceeded {timeout_seconds} seconds")


@contextmanager
def _checker_agent_deadline(timeout_seconds: int):
    """Interrupt one main-thread Agent session while leaving cleanup time."""
    if timeout_seconds <= 0:
        yield
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Checker Agent soft deadline requires main-thread execution")
    previous_handler = signal.getsignal(signal.SIGALRM)

    def handle_timeout(signum, frame):
        del signum, frame
        raise CheckerAgentTimeout(timeout_seconds)

    signal.signal(signal.SIGALRM, handle_timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _install_trajectory_journal(agent: Any, path: Path) -> None:
    """Persist every Agent message so a hard Slurm stop keeps raw evidence."""
    original_add_message = agent.add_message
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")

    def add_message(role: str, content: str, **kwargs: Any) -> None:
        original_add_message(role, content, **kwargs)
        record = redact_sensitive(agent.messages[-1])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    agent.add_message = add_message


def checker_retry_feedback(error: str) -> str:
    """Turn one host validation error into fixed, label-free retry context."""
    return (
        "A previous Checker attempt was rejected by the host output validator.\n"
        f"Validator error: {error}\n"
        "Perform the Checker task again and ensure the final submission satisfies "
        "the required JSON format. This feedback concerns only the output "
        "protocol; it does not change the issue, plan, candidate guideline, "
        "or decision task."
    )


def validate_checker_output(value: dict[str, Any]) -> CheckerOutput:
    predicted = value.get("predicted_resolved")
    reason = value.get("decision_reason")
    evidence = value.get("repository_evidence")
    if not isinstance(predicted, bool):
        raise ValueError("predicted_resolved must be boolean")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("decision_reason must be a non-empty string")
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
    return CheckerOutput(predicted, reason.strip(), tuple(normalized))


def _asi_leakage_categories(
    messages: list[dict[str, Any]],
    case: GEPACase,
) -> list[str]:
    transcript = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    leaked = []
    for key, value in case.asi.items():
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if len(serialized) >= 32 and serialized in transcript:
            leaked.append(key)
    return leaked


class DockerChecker:
    def __init__(
        self,
        config: OptimizationConfig,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self._prepared_images: set[str] = set()
        self._prepare_lock = threading.Lock()

    def prepare(self, case: GEPACase) -> None:
        """Prepare infrastructure before any Checker LLM call."""
        instance_info = case.checker_payload()["repository"]
        image = derive_image_name(instance_info)
        with self._prepare_lock:
            if image in self._prepared_images:
                return
        self.audit.write(
            "checker_infrastructure_prepare_started",
            instance_id=case.instance_id,
            image=image,
        )
        if self.config.container.runtime == "apptainer":
            ApptainerSifCache(
                self.config.container.sif_cache_dir,
                self.capacity_window,
            ).ensure(
                image,
                timeout=self.config.checker.timeout,
            )
        else:
            ensure_project_image_local(
                image,
                timeout=self.config.checker.timeout,
                capacity_window=self.capacity_window,
            )
        self.audit.write(
            "checker_infrastructure_prepare_completed",
            instance_id=case.instance_id,
            image=image,
        )
        with self._prepare_lock:
            self._prepared_images.add(image)

    def __call__(
        self,
        case: GEPACase,
        rules: str,
        *,
        retry_feedback: str = "",
        trajectory_journal_path: Path | None = None,
        output_validator: Callable[
            [dict[str, Any]], CheckerOutput
        ] = validate_checker_output,
        completion_callback: Callable[[CheckerOutput], None] | None = None,
        repository_baseline_dir: Path | None = None,
    ) -> CheckerOutput:
        # Slurm owns the HPC wall-time. The worker only executes and journals
        # evidence; the resumed controller classifies a terminal Slurm state.
        if self.config.execution.backend == "hpc_slurm":
            return self._run_session(
                case,
                rules,
                retry_feedback=retry_feedback,
                trajectory_journal_path=trajectory_journal_path,
                output_validator=output_validator,
                completion_callback=completion_callback,
                repository_baseline_dir=repository_baseline_dir,
            )

        # Local execution has no external scheduler, so its optional soft
        # deadline remains a local transport safeguard.
        with _checker_agent_deadline(self.config.checker.agent_timeout_seconds):
            return self._run_session(
                case,
                rules,
                retry_feedback=retry_feedback,
                trajectory_journal_path=trajectory_journal_path,
                output_validator=output_validator,
                completion_callback=completion_callback,
                repository_baseline_dir=repository_baseline_dir,
            )

    def _run_session(
        self,
        case: GEPACase,
        rules: str,
        *,
        retry_feedback: str = "",
        trajectory_journal_path: Path | None = None,
        output_validator: Callable[
            [dict[str, Any]], CheckerOutput
        ] = validate_checker_output,
        completion_callback: Callable[[CheckerOutput], None] | None = None,
        repository_baseline_dir: Path | None = None,
    ) -> CheckerOutput:
        self.prepare(case)
        DefaultAgent, LitellmModel, _ = import_minisweagent()
        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.checker.model,
                self.config.checker.api_base,
            ),
            model_kwargs={
                "api_key": __import__("os").environ[self.config.checker.api_key_env],
                "api_base": self.config.checker.api_base,
                "temperature": 0.0,
            },
            cost_tracking="ignore_errors",
        )
        candidate_sha256 = text_sha256(rules)
        model = AuditedModel(
            base_model,
            self.usage,
            phase="checker",
            context={
                "instance_id": case.instance_id,
                "candidate_sha256": candidate_sha256,
            },
        )
        instance_info = case.checker_payload()["repository"]
        image = derive_image_name(instance_info)
        checker_payload = case.checker_payload()
        self.audit.write(
            "checker_input_boundary",
            instance_id=case.instance_id,
            candidate_sha256=candidate_sha256,
            candidate_rules_empty=rules == "",
            retry_feedback_present=bool(retry_feedback),
            checker_input_keys=sorted(checker_payload),
            repository_keys=sorted(checker_payload["repository"]),
            forbidden_keys_present=[],
            label_available_to_checker=False,
            asi_available_to_checker=False,
        )
        env: ApptainerEnvironment | DockerEnvWrapper | None = None
        agent: Any | None = None
        try:
            if self.config.container.runtime == "apptainer":
                env = ApptainerEnvironment(
                    image=image,
                    cwd=self.config.docker.workdir,
                    sif_cache_dir=self.config.container.sif_cache_dir,
                    capacity_window=self.capacity_window,
                    timeout=self.config.checker.timeout,
                    writable_tmpfs=self.config.container.writable_tmpfs,
                    git_safe_directories=[self.config.docker.workdir],
                )
            else:
                env = DockerEnvWrapper(self.config.docker, self.capacity_window)
                env.start(
                    image,
                    self.config.docker.workdir,
                    timeout=self.config.checker.timeout,
                    instance_info=instance_info,
                )
            if repository_baseline_dir is not None:
                restore_repository_to_base(
                    env,
                    str(instance_info.get("base_commit", "")),
                    phase="checker",
                    evidence_dir=repository_baseline_dir,
                )
            agent = build_default_agent(
                DefaultAgent,
                model,
                env,
                system_template=self.config.checker_prompt,
                instance_template=self.config.checker_instance_template,
                step_limit=self.config.checker.max_steps,
                cost_limit=self.config.checker.cost_limit,
            )
            if trajectory_journal_path is not None:
                _install_trajectory_journal(agent, trajectory_journal_path)
            exit_status, final_submission = agent.run(
                task=case.issue_description,
                plan=case.plan,
                candidate_guideline=rules,
                candidate_rules=rules,
                retry_feedback=retry_feedback,
            )
            if not final_submission.strip():
                raise CheckerOutputContractError(
                    f"checker did not submit JSON output (exit_status={exit_status})"
                )
            try:
                value = json.loads(final_submission)
                if not isinstance(value, dict):
                    raise ValueError("checker submission must be a JSON object")
                parsed = output_validator(value)
            except (ValueError, json.JSONDecodeError) as exc:
                raise CheckerOutputContractError(
                    "checker final submission invalid "
                    f"(exit_status={exit_status}): {exc}"
                ) from exc
            leaked_categories = _asi_leakage_categories(agent.messages, case)
            self.audit.write(
                "checker_completed",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                predicted_resolved=parsed.predicted_resolved,
                repository_evidence_count=len(parsed.repository_evidence),
                parse_success=True,
                exit_status=exit_status,
                trajectory_messages=len(agent.messages),
                asi_leakage_detected=bool(leaked_categories),
                leaked_asi_categories=leaked_categories,
            )
            if leaked_categories:
                raise RuntimeError(
                    "Checker trajectory contains forbidden ASI content: "
                    + ", ".join(leaked_categories)
                )
            completed = CheckerOutput(
                parsed.predicted_resolved,
                parsed.decision_reason,
                parsed.repository_evidence,
                tuple(agent.messages),
                parsed.revision_feedback,
            )
            # PCCE uses this boundary to make a valid Agent decision durable
            # before disposable-environment cleanup. Existing Offline callers
            # omit the callback and retain identical behavior.
            if completion_callback is not None:
                completion_callback(completed)
            return completed
        except (CheckerAgentTimeout, Exception) as exc:
            if agent is not None:
                try:
                    exc.checker_trajectory = tuple(agent.messages)  # type: ignore[attr-defined]
                except Exception:
                    pass
            raise
        finally:
            if env is not None:
                if isinstance(env, ApptainerEnvironment):
                    env.cleanup()
                else:
                    env.stop()
