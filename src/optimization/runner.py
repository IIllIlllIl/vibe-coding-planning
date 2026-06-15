"""Run the native GEPA search for Checker rules."""

from __future__ import annotations

from typing import Any, Callable

import gepa

from src.environment.docker_env import configure_docker_capacity
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.callbacks import ProgressCallback
from src.optimization.checker import DockerChecker
from src.optimization.config import OptimizationConfig
from src.optimization.dataset import load_snapshot
from src.optimization.reflection import MiniSWEReflectionProposer
from src.optimization.report import write_cost_report, write_report


class OptimizationRunFailed(RuntimeError):
    """Raised when GEPA returns after a swallowed component failure."""


def run_optimization(
    config: OptimizationConfig,
    *,
    checker: Callable[..., Any] | None = None,
    proposer: Callable[..., Any] | None = None,
    optimize_fn: Callable[..., Any] = gepa.optimize,
) -> Any:
    train, validation = load_snapshot(config.dataset_snapshot)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    initial_rules = config.initial_rules_path.read_text(encoding="utf-8").strip()
    audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
    audit.write(
        "run_started",
        seed_candidate_sha256=text_sha256(initial_rules),
        seed_rules_empty=initial_rules == "",
        train_instances=len(train),
        validation_instances=len(validation),
        max_metric_calls=config.search.max_metric_calls,
        projection_metric_calls=config.search.projection_metric_calls,
        reflection_minibatch_size=config.search.reflection_minibatch_size,
        parallel=config.search.parallel,
        seed=config.search.seed,
        candidate_components=["rules"],
        checker_temperature=config.checker.temperature,
        skip_perfect_score=config.search.skip_perfect_score,
        min_proposals=config.search.min_proposals,
        resuming_from_state=(config.run_dir / "gepa_state.bin").is_file(),
        stop_file_present=(config.run_dir / "gepa.stop").is_file(),
    )
    capacity = configure_docker_capacity(
        config.docker,
        max_concurrent=config.search.parallel,
    )
    checker_runner = checker or DockerChecker(config, capacity)
    proposer_runner = proposer or MiniSWEReflectionProposer(config, capacity)
    adapter = CheckerGEPAAdapter(
        checker_runner,
        parallel=config.search.parallel,
        proposer=proposer_runner,
        run_dir=config.run_dir,
        fail_on_checker_error=True,
    )
    callback = ProgressCallback(config.run_dir)
    try:
        result = optimize_fn(
            seed_candidate={"rules": initial_rules},
            trainset=train,
            valset=validation,
            adapter=adapter,
            reflection_lm=None,
            candidate_selection_strategy="pareto",
            frontier_type="instance",
            batch_sampler="epoch_shuffled",
            reflection_minibatch_size=config.search.reflection_minibatch_size,
            perfect_score=1.0,
            skip_perfect_score=config.search.skip_perfect_score,
            max_metric_calls=config.search.max_metric_calls,
            run_dir=str(config.run_dir),
            cache_evaluation=True,
            track_best_outputs=True,
            callbacks=[callback],
            seed=config.search.seed,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        callback.mark_failed(phase="optimization", error=error)
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
        )
        audit.write(
            "run_failed",
            phase="optimization",
            error=error,
        )
        raise
    write_report(result, validation, config.run_dir)
    failures = list(getattr(proposer_runner, "failures", []))
    successful_proposals = int(
        getattr(proposer_runner, "successful_proposals", 0)
    )
    failure_reasons = []
    if failures:
        failure_reasons.append(
            f"{len(failures)} Reflection proposal attempt(s) failed"
        )
    if successful_proposals < config.search.min_proposals:
        failure_reasons.append(
            "successful Reflection proposals "
            f"{successful_proposals} < required {config.search.min_proposals}"
        )
    run_status = "failed" if failure_reasons else "completed"
    write_cost_report(
        config.run_dir,
        observed_metric_calls=int(result.total_metric_calls or 0),
        projection_metric_calls=config.search.projection_metric_calls,
        parallel=config.search.parallel,
        run_status=run_status,
        successful_proposals=successful_proposals,
        required_proposals=config.search.min_proposals,
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
    audit.write(
        "run_completed",
        best_candidate_idx=result.best_idx,
        best_candidate_sha256=text_sha256(result.best_candidate["rules"]),
        best_rules_empty=result.best_candidate["rules"] == "",
        total_metric_calls=result.total_metric_calls,
        candidate_count=result.num_candidates,
        successful_proposals=successful_proposals,
        required_proposals=config.search.min_proposals,
        stop_file_present=(config.run_dir / "gepa.stop").is_file(),
    )
    return result
