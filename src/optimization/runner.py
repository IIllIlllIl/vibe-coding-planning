"""Run the native GEPA search for Checker rules."""

from __future__ import annotations

from typing import Any, Callable

import gepa
from gepa.utils import MaxCandidateProposalsStopper

from src.environment.docker_env import configure_docker_capacity
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.callbacks import ProgressCallback
from src.optimization.checker import DockerChecker
from src.optimization.config import OptimizationConfig
from src.optimization.dataset import GEPACaseLoader, load_snapshot
from src.optimization.reflection import MiniSWEReflectionProposer
from src.optimization.report import write_cost_report, write_report
from src.optimization.resume import (
    ReproducibleSearchState,
    load_seed_validation_replay,
    prepare_run_manifest,
)


class OptimizationRunFailed(RuntimeError):
    """Raised when GEPA returns after a swallowed component failure."""


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
    config.run_dir.mkdir(parents=True, exist_ok=True)
    initial_rules = config.initial_rules_path.read_text(encoding="utf-8").strip()
    resuming = prepare_run_manifest(config, initial_rules=initial_rules)
    search_state = ReproducibleSearchState(config, resuming=resuming)
    audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
    audit.write(
        "run_started",
        seed_candidate_sha256=text_sha256(initial_rules),
        seed_rules_empty=initial_rules == "",
        train_instances=len(train),
        validation_instances=len(validation),
        max_metric_calls=config.search.max_metric_calls,
        max_iterations=config.search.max_iterations,
        projection_metric_calls=config.search.projection_metric_calls,
        reflection_minibatch_size=config.search.reflection_minibatch_size,
        parallel=config.search.parallel,
        seed=config.search.seed,
        candidate_components=["rules"],
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
    proposer_runner = proposer or MiniSWEReflectionProposer(
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
    )
    callback = ProgressCallback(
        config.run_dir,
        checkpoint=search_state,
        proposer=proposer_runner,
        accepted_candidates=search_state.accepted_candidates,
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
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        resumable, failure_kind = _classify_optimization_failure(error)
        callback.mark_failed(
            phase="optimization",
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
            phase="optimization",
            error=error,
            resumable=resumable,
            failure_kind=failure_kind,
        )
        raise
    write_report(result, validation, config.run_dir)
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
        best_rules_empty=result.best_candidate["rules"] == "",
        total_metric_calls=result.total_metric_calls,
        candidate_count=result.num_candidates,
        successful_proposals=successful_proposals,
        required_proposals=config.search.min_proposals,
        reflection_failures=len(failures),
        stop_file_present=(config.run_dir / "gepa.stop").is_file(),
    )
    return result
