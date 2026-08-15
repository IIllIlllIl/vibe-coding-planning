"""Run the native GEPA search for an Offline plan-review guideline."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable

import gepa
from gepa.core.state import GEPAState
from gepa.utils import MaxCandidateProposalsStopper

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.callbacks import ProgressCallback
from src.optimization.checker import DockerChecker
from src.optimization.config import OptimizationConfig
from src.optimization.dataset import GEPACaseLoader, load_snapshot
from src.optimization.offline_hpc_executor import (
    HPCSlurmOfflineCheckerExecutor,
)
from src.optimization.offline_hpc_reflection import (
    HPCOfflineReflectionProposer,
)
from src.optimization.reflection import MiniSWEReflectionProposer
from src.optimization.report import write_cost_report, write_report
from src.optimization.resume import (
    ReproducibleSearchState,
    load_seed_validation_replay,
    prepare_run_manifest,
)


class OptimizationRunFailed(RuntimeError):
    """Raised when GEPA returns after a swallowed component failure."""


def _atomic_status(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def _offline_controller_lock(config: OptimizationConfig):
    lock_path = config.run_dir / "controller.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"another GEPA controller is active for {config.run_dir}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _classify_optimization_failure(error: str) -> tuple[bool, str | None]:
    if "Checker operational failure for:" in error:
        return True, "checker_operational_failure"
    return False, None


def run_optimization(
    config: OptimizationConfig,
    *,
    checker: Callable[..., Any] | None = None,
    proposer: Callable[..., Any] | None = None,
    optimize_fn: Callable[..., Any] = gepa.optimize,
) -> Any:
    config.run_dir.mkdir(parents=True, exist_ok=True)
    with _offline_controller_lock(config):
        return _run_optimization_locked(
            config,
            checker=checker,
            proposer=proposer,
            optimize_fn=optimize_fn,
        )


def _run_optimization_locked(
    config: OptimizationConfig,
    *,
    checker: Callable[..., Any] | None,
    proposer: Callable[..., Any] | None,
    optimize_fn: Callable[..., Any],
) -> Any:
    train, validation = load_snapshot(config.dataset_snapshot)
    class_counts_by_split = {
        "train": {
            True: sum(case.resolved for case in train),
            False: sum(not case.resolved for case in train),
        },
        "validation": {
            True: sum(case.resolved for case in validation),
            False: sum(not case.resolved for case in validation),
        },
    }
    train_loader = GEPACaseLoader(train)
    validation_loader = GEPACaseLoader(validation)
    _atomic_status(
        config.run_dir / "controller_status.json",
        {"schema_version": 1, "status": "running", "pid": os.getpid()},
    )
    initial_rules = config.initial_rules_path.read_text(encoding="utf-8").strip()
    resuming = prepare_run_manifest(config, initial_rules=initial_rules)
    search_state = ReproducibleSearchState(config, resuming=resuming)
    audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
    audit.write(
        "run_started",
        seed_candidate_sha256=text_sha256(initial_rules),
        seed_guideline_empty=initial_rules == "",
        train_instances=len(train),
        validation_instances=len(validation),
        max_metric_calls=config.search.max_metric_calls,
        max_iterations=config.search.max_iterations,
        projection_metric_calls=config.search.projection_metric_calls,
        reflection_minibatch_size=config.search.reflection_minibatch_size,
        train_case_repetitions=config.search.train_case_repetitions,
        parallel=config.search.parallel,
        seed=config.search.seed,
        candidate_components=["guideline"],
        checker_temperature=config.checker.temperature,
        skip_perfect_score=config.search.skip_perfect_score,
        min_proposals=config.search.min_proposals,
        primary_metric=config.search.primary_metric,
        class_counts_by_split=class_counts_by_split,
        resuming_from_state=resuming,
        stop_file_present=(config.run_dir / "gepa.stop").is_file(),
    )
    capacity = configure_docker_capacity(
        config.docker,
        max_concurrent=config.search.parallel,
    )
    checker_runner = checker or DockerChecker(config, capacity)
    checker_batch_executor = (
        HPCSlurmOfflineCheckerExecutor(config)
        if checker is None and config.execution.backend == "hpc_slurm"
        else None
    )
    if proposer is not None:
        proposer_runner = proposer
    elif config.execution.backend == "hpc_slurm":
        proposer_runner = HPCOfflineReflectionProposer(
            config,
            successful_proposals=search_state.successful_proposals,
            failures=search_state.reflection_failures,
        )
    else:
        proposer_runner = MiniSWEReflectionProposer(
            config,
            capacity,
            successful_proposals=search_state.successful_proposals,
            failures=search_state.reflection_failures,
        )
    if proposer is not None:
        proposer_runner.successful_proposals = search_state.successful_proposals
        proposer_runner.failures = list(search_state.reflection_failures)
    seed_replay = (
        load_seed_validation_replay(
            config.run_dir,
            validation,
            initial_rules=initial_rules,
        )
        if resuming
        else None
    )
    adapter = CheckerGEPAAdapter(
        checker_runner,
        parallel=config.search.parallel,
        proposer=proposer_runner,
        run_dir=config.run_dir,
        checker_attempts=config.checker.max_attempts,
        startup_seed_replay=seed_replay,
        seed_rules_sha256=text_sha256(initial_rules),
        primary_metric=config.search.primary_metric,
        class_counts_by_split=class_counts_by_split,
        batch_executor=checker_batch_executor,
        train_case_repetitions=config.search.train_case_repetitions,
    )
    callback = ProgressCallback(
        config.run_dir,
        checkpoint=search_state,
        proposer=proposer_runner,
        accepted_candidates=search_state.accepted_candidates,
        completed_iterations=(
            max(0, GEPAState.load(str(config.run_dir)).i + 1)
            if (config.run_dir / "gepa_state.bin").is_file()
            else 0
        ),
    )
    iteration_stopper = (
        MaxCandidateProposalsStopper(config.search.max_iterations)
        if config.search.max_iterations is not None
        else None
    )
    try:
        result = optimize_fn(
            seed_candidate={"rules": initial_rules},
            trainset=train_loader,
            valset=validation_loader,
            adapter=adapter,
            reflection_lm=None,
            candidate_selection_strategy=search_state.selector,
            frontier_type="instance",
            batch_sampler=search_state.sampler,
            reflection_minibatch_size=None,
            perfect_score=1.0,
            skip_perfect_score=config.search.skip_perfect_score,
            max_metric_calls=config.search.max_metric_calls,
            stop_callbacks=iteration_stopper,
            run_dir=str(config.run_dir),
            cache_evaluation=True,
            track_best_outputs=True,
            callbacks=[callback],
            seed=config.search.seed,
        )
    except ControllerYield as exc:
        _atomic_status(
            config.run_dir / "controller_status.json",
            {
                "schema_version": 1,
                "status": "yielded",
                "reason": exc.reason,
                "batch_dir": exc.batch_dir,
                "worker_job_id": exc.job_id,
            },
        )
        audit.write(
            "offline_controller_yielded",
            reason=exc.reason,
            batch_dir=exc.batch_dir,
            worker_job_id=exc.job_id,
        )
        return None
    except (OfflineReflectionBlocked, Exception) as caught:
        exc = (
            caught.cause
            if isinstance(caught, OfflineReflectionBlocked)
            else caught
        )
        error = f"{type(exc).__name__}: {exc}"
        if isinstance(caught, OfflineReflectionBlocked):
            resumable = False
            failure_kind = "reflection_task_blocked"
            failure_phase = "reflection"
        else:
            resumable, failure_kind = _classify_optimization_failure(error)
            failure_phase = "optimization"
        callback.mark_failed(
            phase=failure_phase,
            error=error,
            resumable=resumable,
            failure_kind=failure_kind,
        )
        JsonlLogger(config.run_dir / "errors.jsonl").write(
            "optimization_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        write_cost_report(
            config.run_dir,
            observed_metric_calls=int(
                callback.progress.get("metric_calls_used", 0)
            ),
            projection_metric_calls=config.search.projection_metric_calls,
            parallel=config.search.parallel,
            run_status="failed",
            successful_proposals=int(
                getattr(proposer_runner, "successful_proposals", 0)
            ),
            required_proposals=config.search.min_proposals,
            reflection_failures=len(
                getattr(proposer_runner, "failures", [])
            ),
        )
        audit.write(
            "run_failed",
            phase=failure_phase,
            error=error,
            resumable=resumable,
            failure_kind=failure_kind,
        )
        _atomic_status(
            config.run_dir / "controller_status.json",
            {
                "schema_version": 1,
                "status": "retryable_failed" if resumable else "failed",
                "failure_phase": failure_phase,
                "blocking": not resumable,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    write_report(
        result,
        validation,
        config.run_dir,
        primary_metric=config.search.primary_metric,
    )
    failures = list(getattr(proposer_runner, "failures", []))
    successful_proposals = int(
        getattr(proposer_runner, "successful_proposals", 0)
    )
    failure_reasons = []
    if successful_proposals < config.search.min_proposals:
        failure_reasons.append(
            "successful Reflection proposals "
            f"{successful_proposals} < required {config.search.min_proposals}"
        )
    run_status = (
        "failed"
        if failure_reasons
        else "completed_with_warnings"
        if failures
        else "completed"
    )
    write_cost_report(
        config.run_dir,
        observed_metric_calls=int(result.total_metric_calls or 0),
        projection_metric_calls=config.search.projection_metric_calls,
        parallel=config.search.parallel,
        run_status=run_status,
        successful_proposals=successful_proposals,
        required_proposals=config.search.min_proposals,
        reflection_failures=len(failures),
    )
    if failure_reasons:
        error = "; ".join(failure_reasons)
        callback.mark_failed(phase="reflection", error=error)
        audit.write(
            "run_failed",
            phase="reflection",
            error=error,
            failures=failures,
            successful_proposals=successful_proposals,
            required_proposals=config.search.min_proposals,
        )
        raise OptimizationRunFailed(error)
    if failures:
        callback.mark_completed_with_warnings(
            warning=(
                f"{len(failures)} Reflection proposal attempt(s) failed; "
                "GEPA continued because the successful proposal threshold was met"
            ),
            reflection_failures=len(failures),
        )
    audit.write(
        "run_completed",
        best_candidate_idx=result.best_idx,
        best_candidate_sha256=text_sha256(result.best_candidate["rules"]),
        best_guideline_empty=result.best_candidate["rules"] == "",
        total_metric_calls=result.total_metric_calls,
        candidate_count=result.num_candidates,
        successful_proposals=successful_proposals,
        required_proposals=config.search.min_proposals,
        reflection_failures=len(failures),
        stop_file_present=(config.run_dir / "gepa.stop").is_file(),
    )
    _atomic_status(
        config.run_dir / "controller_status.json",
        {"schema_version": 1, "status": run_status},
    )
    return result
