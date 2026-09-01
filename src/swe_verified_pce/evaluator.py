"""Official SWE-Verified grading in a clean, identity-bound SIF workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.environment.repository_baseline import restore_repository_to_base
from src.exceptions import FatalError
from src.optimization.config import ContainerConfig
from src.swe_verified_pce.models import SWEVerifiedPCECase


APPLY_METHODS = (
    ("git_apply", "git apply --check", "git apply --verbose"),
    ("git_apply_reject", "git apply --check", "git apply --verbose --reject"),
    (
        "patch",
        "patch --dry-run --batch --fuzz=5 -p1 -i",
        "patch --batch --fuzz=5 -p1 -i",
    ),
)
COMMAND_NOT_EXECUTED_RETURNCODES = frozenset({126, 127})


class SWEVerifiedEvaluatorOperationalError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        outcome_reason: str,
        retry_disposition: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.outcome_reason = outcome_reason
        self.retry_disposition = retry_disposition
        self.evidence = evidence or {}


def _terminal(
    *,
    outcome: str,
    reason: str,
    evaluator_resolved: bool | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "completed",
        "classification_policy": "swe_verified_pce_outcomes_v1",
        "terminal_kind": reason,
        "task_outcome": outcome,
        "outcome_reason": reason,
        "retry_disposition": "no_retry",
        "evaluator_resolved": evaluator_resolved,
        **evidence,
    }


def _apply_patch(
    env: ApptainerEnvironment,
    filename: str,
    *,
    command_timeout: int,
) -> tuple[bool, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for method, check_prefix, apply_prefix in APPLY_METHODS:
        check = env.execute(f"{check_prefix} {filename}", timeout=command_timeout)
        applied: dict[str, Any] | None = None
        if check.get("returncode") == 0:
            applied = env.execute(f"{apply_prefix} {filename}", timeout=command_timeout)
        attempts.append(
            {
                "method": method,
                "preflight_returncode": check.get("returncode"),
                "preflight_output": check.get("output", ""),
                "apply_returncode": applied.get("returncode") if applied else None,
                "apply_output": applied.get("output", "") if applied else "",
            }
        )
        if applied is not None and applied.get("returncode") == 0:
            return True, attempts
    return False, attempts


def evaluate_swe_verified_apptainer(
    patch: str,
    case: SWEVerifiedPCECase,
    *,
    container: ContainerConfig,
    capacity_window: DockerCapacityWindow,
    workdir: str,
    phase_workdir: Path,
    command_timeout: int,
    evaluation_started_callback: Callable[[], None] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
    cleanup_error_callback: Callable[[BaseException], None] | None = None,
    repository_baseline_dir: Path | None = None,
) -> dict[str, Any]:
    """Run official SWE-bench grading while preserving outcome uncertainty."""

    def completed(result: dict[str, Any]) -> dict[str, Any]:
        if result_callback is not None:
            result_callback(result)
        return result

    try:
        from swebench.harness.grading import get_eval_report
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise SWEVerifiedEvaluatorOperationalError(
            f"official SWE-bench evaluator is unavailable: {exc}",
            outcome_reason="evaluator_package_unavailable",
            retry_disposition="block_run",
        ) from exc

    env: ApptainerEnvironment | None = None
    try:
        phase_workdir.mkdir(parents=True, exist_ok=True)
        env = ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=workdir,
            sif_cache_dir=container.sif_cache_dir,
            capacity_window=capacity_window,
            timeout=None,
            writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir],
            host_workdir=phase_workdir,
            initialize_host_workdir=True,
        )
        try:
            restore_repository_to_base(
                env,
                case.base_commit,
                phase="evaluate",
                evidence_dir=repository_baseline_dir
                or phase_workdir.parent / "evaluate_repository_baseline",
            )
        except FatalError as exc:
            raise SWEVerifiedEvaluatorOperationalError(
                f"evaluator could not restore the frozen base repository: {exc}",
                outcome_reason="repository_reset_failed",
                retry_disposition="block_run",
            ) from exc
        except Exception as exc:
            raise SWEVerifiedEvaluatorOperationalError(
                f"evaluator repository restore did not execute: {exc}",
                outcome_reason="repository_reset_failed",
                retry_disposition="retry_same_phase",
            ) from exc

        try:
            test_spec = make_test_spec(case.evaluator_input(), namespace="swebench")
        except Exception as exc:
            raise SWEVerifiedEvaluatorOperationalError(
                f"official SWE-bench test spec could not be constructed: {exc}",
                outcome_reason="test_spec_failed",
                retry_disposition="block_run",
            ) from exc

        code_path = phase_workdir / ".vibe_code.patch"
        eval_path = phase_workdir / ".vibe_eval.sh"
        output_path = phase_workdir / ".vibe_test_output.txt"
        code_path.write_text(patch, encoding="utf-8")
        eval_path.write_text(test_spec.eval_script, encoding="utf-8")

        if not patch.strip():
            return completed(
                _terminal(
                    outcome="unresolved",
                    reason="empty_generation",
                    evaluator_resolved=False,
                    evidence={"code_patch_applied": False, "code_patch_attempts": []},
                )
            )
        applied, attempts = _apply_patch(
            env, ".vibe_code.patch", command_timeout=command_timeout
        )
        if not applied:
            return completed(
                _terminal(
                    outcome="unresolved",
                    reason="code_patch_not_applied",
                    evaluator_resolved=False,
                    evidence={
                        "code_patch_applied": False,
                        "code_patch_attempts": attempts,
                    },
                )
            )
        if evaluation_started_callback is not None:
            evaluation_started_callback()
        # The Slurm task walltime is the only evaluator deadline. This lets
        # the resumed controller distinguish a hard evaluator TIMEOUT from a
        # scored test failure using durable phase checkpoints.
        test_result = env.execute("/bin/bash .vibe_eval.sh", timeout=None)
        raw_output = str(test_result.get("output", ""))
        output_path.write_text(raw_output, encoding="utf-8")
        returncode = test_result.get("returncode")
        if returncode in COMMAND_NOT_EXECUTED_RETURNCODES:
            raise SWEVerifiedEvaluatorOperationalError(
                f"official SWE-bench test command did not execute (returncode={returncode})",
                outcome_reason="test_command_not_executed",
                retry_disposition="retry_same_phase",
                evidence={"raw_test_output": raw_output, "test_returncode": returncode},
            )
        try:
            report = get_eval_report(
                test_spec=test_spec,
                prediction={
                    "instance_id": case.instance_id,
                    "model_patch": patch,
                    "model_name_or_path": "swe-verified-pce",
                },
                test_log_path=output_path,
                include_tests_status=True,
            )
        except Exception as exc:
            return completed(
                _terminal(
                    outcome="unknown",
                    reason="grading_failed",
                    evaluator_resolved=None,
                    evidence={
                        "code_patch_applied": True,
                        "code_patch_attempts": attempts,
                        "raw_test_output": raw_output,
                        "test_returncode": returncode,
                        "grading_error_type": type(exc).__name__,
                        "grading_error": str(exc),
                    },
                )
            )
        instance_report = report.get(case.instance_id)
        if not isinstance(instance_report, dict) or not isinstance(
            instance_report.get("resolved"), bool
        ):
            return completed(
                _terminal(
                    outcome="unknown",
                    reason="grading_result_missing",
                    evaluator_resolved=None,
                    evidence={
                        "official_report": report,
                        "raw_test_output": raw_output,
                        "test_returncode": returncode,
                    },
                )
            )
        resolved = bool(instance_report["resolved"])
        return completed(
            _terminal(
                outcome="resolved" if resolved else "unresolved",
                reason="official_tests_resolved"
                if resolved
                else "official_tests_unresolved",
                evaluator_resolved=resolved,
                evidence={
                    "official_report": report,
                    "raw_test_output": raw_output,
                    "test_returncode": returncode,
                    "test_timed_out": False,
                    "code_patch_applied": True,
                    "code_patch_attempts": attempts,
                },
            )
        )
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception as exc:
                if cleanup_error_callback is not None:
                    cleanup_error_callback(exc)
                else:
                    raise
