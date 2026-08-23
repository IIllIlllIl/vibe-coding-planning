"""Official PolyBench parsing and scoring in a fresh Apptainer workspace."""

from __future__ import annotations

from dataclasses import asdict
import importlib
from pathlib import Path
from typing import Any, Callable

from src.environment.apptainer_env import ApptainerEnvironment
from src.exceptions import CommandTimeoutError
from src.optimization.config import ContainerConfig
from src.environment.docker_env import DockerCapacityWindow
from src.polybench_pce.config import DependencyCacheConfig
from src.polybench_pce.models import PolyBenchPCECase


class PolyBenchEvaluatorOperationalError(RuntimeError):
    """The evaluator could not produce an official terminal observation."""

    def __init__(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
        outcome_reason: str = "evaluator_operational_failure",
        retry_disposition: str = "retry_same_phase",
    ) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})
        self.outcome_reason = outcome_reason
        self.retry_disposition = retry_disposition


APPLY_METHODS = (
    (
        "git_apply",
        "git apply --check --ignore-whitespace",
        "git apply --verbose --ignore-whitespace",
    ),
    (
        "patch_fuzz5",
        "patch --dry-run --batch --fuzz=5 -p1 -f -i",
        "patch --batch --fuzz=5 -p1 -f -i",
    ),
)

# POSIX shells use 126 when a command was found but could not be executed and
# 127 when it could not be found.  Neither is evidence that the submitted code
# failed the benchmark tests: the tests did not start.
COMMAND_NOT_EXECUTED_RETURNCODES = frozenset({126, 127})


def _dependency_cache_runtime(
    case: PolyBenchPCECase,
    dependency_cache: DependencyCacheConfig,
) -> tuple[list[str], bool]:
    if case.instance_id not in dependency_cache.included_instances:
        raise PolyBenchEvaluatorOperationalError(
            f"instance is outside frozen dependency-cache membership: {case.instance_id}",
            outcome_reason="dependency_cache_instance_excluded",
            retry_disposition="block_run",
        )
    sif_hashes = dict(dependency_cache.instance_sif_sha256)
    if sif_hashes.get(case.instance_id) != case.image.sif_sha256:
        raise PolyBenchEvaluatorOperationalError(
            "dependency cache and evaluator SIF identities differ",
            outcome_reason="dependency_cache_sif_mismatch",
            retry_disposition="block_run",
        )
    host_cache = (
        dependency_cache.remote_cache_root
        / "instances"
        / case.instance_id
        / "dependency-cache"
    )
    if not host_cache.is_dir():
        raise PolyBenchEvaluatorOperationalError(
            f"frozen dependency cache is missing: {host_cache}",
            outcome_reason="dependency_cache_missing",
            retry_disposition="block_run",
        )
    run_args = ["--bind", f"{host_cache}:/dependency-cache:ro"]
    for key, value in {
        "HF_HOME": "/dependency-cache",
        "HF_HUB_CACHE": "/dependency-cache/hub",
        "HUGGINGFACE_HUB_CACHE": "/dependency-cache/hub",
        "TRANSFORMERS_CACHE": "/dependency-cache/hub",
        "SENTENCE_TRANSFORMERS_HOME": "/dependency-cache/sentence_transformers",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
    }.items():
        run_args.extend(["--env", f"{key}={value}"])
    return run_args, dependency_cache.network_disabled


def _terminal_result(
    case: PolyBenchPCECase,
    *,
    task_outcome: str,
    outcome_reason: str,
    generation: bool,
    patch_applied: bool,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Build a terminal result while retaining the official score semantics."""
    from poly_bench_evaluation.scoring import instance_level_scoring

    score = None
    evaluator_resolved: bool | None = None
    if task_outcome in {"resolved", "unresolved"}:
        score = instance_level_scoring(
            instance_id=case.instance_id,
            result={},
            f2p=list(case.f2p),
            p2p=list(case.p2p),
            patch_applied=patch_applied,
            generation=generation,
        )
        evaluator_resolved = bool(score.resolved)
    return {
        "status": "completed",
        "classification_policy": "polybench_pce_outcomes_v2",
        "terminal_kind": outcome_reason,
        "task_outcome": task_outcome,
        "outcome_reason": outcome_reason,
        "retry_disposition": "no_retry",
        "evaluator_resolved": evaluator_resolved,
        "official_score": asdict(score) if score is not None else None,
        **evidence,
    }


def _apply_patch(
    env: ApptainerEnvironment,
    filename: str,
    *,
    timeout: int,
) -> tuple[bool, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for method, check_prefix, apply_prefix in APPLY_METHODS:
        check = env.execute(f"{check_prefix} {filename}", timeout=timeout)
        result: dict[str, Any] | None = None
        if check.get("returncode") == 0:
            result = env.execute(f"{apply_prefix} {filename}", timeout=timeout)
        status = env.execute("git status --porcelain=v1", timeout=timeout)
        diff = env.execute("git diff --binary --full-index", timeout=timeout)
        attempts.append(
            {
                "method": method,
                "preflight_command": f"{check_prefix} {filename}",
                "preflight_returncode": check.get("returncode"),
                "preflight_output": check.get("output", ""),
                "apply_command": f"{apply_prefix} {filename}",
                "apply_returncode": result.get("returncode") if result else None,
                "apply_output": result.get("output", "") if result else "",
                "repository_status_after": status,
                "repository_diff_after": diff,
            }
        )
        if result is not None and result.get("returncode") == 0:
            return True, attempts
    return False, attempts


def evaluate_polybench_apptainer(
    patch: str,
    case: PolyBenchPCECase,
    *,
    container: ContainerConfig,
    capacity_window: DockerCapacityWindow,
    workdir: str,
    phase_workdir: Path,
    timeout: int,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
    cleanup_error_callback: Callable[[BaseException], None] | None = None,
    dependency_cache: DependencyCacheConfig | None = None,
) -> dict[str, Any]:
    """Return raw official evidence without deciding validation inclusion."""

    def completed(result: dict[str, Any]) -> dict[str, Any]:
        if dependency_cache is not None:
            result["dependency_cache"] = {
                "manifest_sha256": dependency_cache.manifest_sha256,
                "network_disabled": dependency_cache.network_disabled,
                "container_path": "/dependency-cache",
            }
        if result_callback is not None:
            result_callback(result)
        return result

    try:
        from poly_bench_evaluation.constants import REPO_TO_PARSER_CLASS
        from poly_bench_evaluation.scoring import instance_level_scoring
    except ImportError as exc:
        raise PolyBenchEvaluatorOperationalError(
            f"official PolyBench evaluator is unavailable: {exc}",
            outcome_reason="evaluator_package_unavailable",
            retry_disposition="block_run",
        ) from exc

    parser_name = REPO_TO_PARSER_CLASS.get(case.repo)
    if not parser_name:
        raise PolyBenchEvaluatorOperationalError(
            f"official parser is unavailable for repository {case.repo}",
            outcome_reason="parser_unavailable",
            retry_disposition="block_run",
        )
    env: ApptainerEnvironment | None = None
    try:
        run_args: list[str] = []
        network_disabled = False
        if dependency_cache is not None:
            run_args, network_disabled = _dependency_cache_runtime(
                case, dependency_cache
            )
        # The directory must be empty when ApptainerEnvironment is created:
        # that constructor materializes the image's /testbed into it.  Only
        # then do we add evaluator-owned inputs to the bound workspace.
        phase_workdir.mkdir(parents=True, exist_ok=True)
        env = ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=workdir,
            sif_cache_dir=container.sif_cache_dir,
            capacity_window=capacity_window,
            timeout=timeout,
            writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir],
            host_workdir=phase_workdir,
            initialize_host_workdir=True,
            run_args=run_args,
            network_disabled=network_disabled,
        )
        repository_check = env.execute(
            "git rev-parse --is-inside-work-tree >/dev/null "
            f"&& git reset --hard {case.base_commit} && git clean -fd",
            timeout=120,
        )
        if repository_check.get("returncode") != 0:
            raise PolyBenchEvaluatorOperationalError(
                "evaluator could not reset the frozen base repository: "
                + str(repository_check.get("output", ""))[:1000],
                evidence={"repository_reset": repository_check},
                outcome_reason="repository_reset_failed",
            )

        # These evaluator-owned files must be created after ``git clean -fd``.
        # Writing them before the reset makes the cleanup delete its own inputs
        # and deterministically turns every evaluation into an operational
        # ``No such file or directory`` failure.
        (phase_workdir / ".vibe_test.patch").write_text(
            case.test_patch, encoding="utf-8"
        )
        (phase_workdir / ".vibe_code.patch").write_text(patch, encoding="utf-8")
        (phase_workdir / ".vibe_eval.sh").write_text(
            case.test_command + "\n", encoding="utf-8"
        )

        test_patch_applied, test_patch_attempts = _apply_patch(
            env, ".vibe_test.patch", timeout=timeout
        )
        if not test_patch_applied:
            return completed(
                _terminal_result(
                    case,
                    task_outcome="unknown",
                    outcome_reason="test_patch_not_applied",
                    generation=bool(patch.strip()),
                    patch_applied=False,
                    evidence={
                        "terminal_kind": "test_patch_not_applied",
                        "test_patch_attempts": test_patch_attempts,
                    },
                )
            )

        generation = bool(patch.strip())
        code_patch_attempts: list[dict[str, Any]] = []
        code_patch_applied = False
        if generation:
            code_patch_applied, code_patch_attempts = _apply_patch(
                env, ".vibe_code.patch", timeout=timeout
            )
        if not generation or not code_patch_applied:
            kind = "empty_generation" if not generation else "code_patch_not_applied"
            return completed(
                _terminal_result(
                    case,
                    task_outcome="unresolved",
                    outcome_reason=kind,
                    generation=generation,
                    patch_applied=False,
                    evidence={
                        "terminal_kind": kind,
                        "test_patch_applied": True,
                        "test_patch_attempts": test_patch_attempts,
                        "code_patch_applied": False,
                        "code_patch_attempts": code_patch_attempts,
                        "test_command": case.test_command,
                    },
                )
            )

        try:
            test_result = env.execute("/bin/bash .vibe_eval.sh", timeout=timeout)
            raw_output = str(test_result.get("output", ""))
            test_returncode = test_result.get("returncode")
        except CommandTimeoutError as exc:
            return completed(
                _terminal_result(
                    case,
                    task_outcome="unresolved",
                    outcome_reason="test_execution_timeout",
                    generation=True,
                    patch_applied=True,
                    evidence={
                        "terminal_kind": "test_timeout",
                        "test_patch_applied": True,
                        "test_patch_attempts": test_patch_attempts,
                        "code_patch_applied": True,
                        "code_patch_attempts": code_patch_attempts,
                        "test_command": case.test_command,
                        "raw_test_output": str(exc),
                        "test_timed_out": True,
                    },
                )
            )

        if test_returncode in COMMAND_NOT_EXECUTED_RETURNCODES:
            raise PolyBenchEvaluatorOperationalError(
                "official PolyBench test command did not execute "
                f"(returncode={test_returncode})",
                evidence={
                    "terminal_kind": "test_command_not_executed",
                    "test_patch_applied": True,
                    "test_patch_attempts": test_patch_attempts,
                    "code_patch_applied": True,
                    "code_patch_attempts": code_patch_attempts,
                    "test_command": case.test_command,
                    "test_returncode": test_returncode,
                    "raw_test_output": raw_output,
                    "test_timed_out": False,
                },
                outcome_reason="test_command_not_executed",
                retry_disposition="retry_same_phase",
            )

        parsed: dict[str, Any] = {}
        parsers = importlib.import_module("poly_bench_evaluation.parsers")
        if not hasattr(parsers, parser_name):
            raise PolyBenchEvaluatorOperationalError(
                f"official parser class is missing: {parser_name}",
                evidence={
                    "terminal_kind": "parser_missing",
                    "parser_name": parser_name,
                    "raw_test_output": raw_output,
                    "test_returncode": test_returncode,
                },
                outcome_reason="parser_missing",
                retry_disposition="block_run",
            )
        try:
            parsed = getattr(parsers, parser_name)(test_content=raw_output).parse()
        except Exception as exc:
            return completed(
                _terminal_result(
                    case,
                    task_outcome="unknown",
                    outcome_reason="parser_failed",
                    generation=True,
                    patch_applied=True,
                    evidence={
                        "terminal_kind": "parser_failed",
                        "parser_name": parser_name,
                        "raw_test_output": raw_output,
                        "test_returncode": test_returncode,
                        "parser_error_type": type(exc).__name__,
                        "parser_error": str(exc),
                    },
                )
            )
        score = instance_level_scoring(
            instance_id=case.instance_id,
            result=parsed,
            f2p=list(case.f2p),
            p2p=list(case.p2p),
            patch_applied=True,
            generation=True,
        )
        return completed(
            {
                "status": "completed",
                "classification_policy": "polybench_pce_outcomes_v2",
                "terminal_kind": "tests_parsed",
                "task_outcome": "resolved" if score.resolved else "unresolved",
                "outcome_reason": (
                    "tests_parsed_resolved"
                    if score.resolved
                    else "tests_parsed_unresolved"
                ),
                "retry_disposition": "no_retry",
                "evaluator_resolved": bool(score.resolved),
                "official_score": asdict(score),
                "test_patch_applied": True,
                "test_patch_attempts": test_patch_attempts,
                "code_patch_applied": True,
                "code_patch_attempts": code_patch_attempts,
                "test_command": case.test_command,
                "test_returncode": test_returncode,
                "test_timed_out": False,
                "raw_test_output": raw_output,
                "parsed_test_result": parsed,
            }
        )
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception as exc:
                if cleanup_error_callback is not None:
                    cleanup_error_callback(exc)
