"""Run Behavioral Plan Acceptability through the retained GEPA search."""

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

from src.exceptions import ControllerYield, OfflineReflectionBlocked
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.behavioral_adapter import BehavioralGEPAAdapter
from src.optimization.behavioral_dataset import (
    BehavioralCaseLoader,
    load_behavioral_snapshot,
)
from src.optimization.behavioral_hpc_executor import (
    HPCSlurmBehavioralCheckerExecutor,
)
from src.optimization.behavioral_report import write_behavioral_report
from src.optimization.behavioral_runtime import (
    BehavioralLocalChecker,
    BehavioralLocalReflectionProposer,
)
from src.optimization.callbacks import ProgressCallback
from src.optimization.config import OptimizationConfig
from src.optimization.offline_hpc_reflection import HPCOfflineReflectionProposer
from src.optimization.report import write_cost_report
from src.optimization.resume import ReproducibleSearchState, prepare_run_manifest


def _atomic_status(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


@contextmanager
def _controller_lock(config: OptimizationConfig):
    path = config.run_dir / "controller.lock"
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Behavioral controller is active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _seed_validation_replay(
    config: OptimizationConfig,
    validation: list[Any],
    guideline: str,
) -> dict[str, tuple[dict[str, Any], float]]:
    path = config.run_dir / "behavioral_evaluations.jsonl"
    expected = {case.instance_id for case in validation}
    candidate_sha256 = text_sha256(guideline)
    replay = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            instance_id = str(record.get("instance_id", ""))
            if (
                record.get("candidate_sha256") == candidate_sha256
                and record.get("split") == "validation"
                and instance_id in expected
            ):
                replay[instance_id] = (
                    dict(record["output"]),
                    float(record["score"]),
                )
    if set(replay) != expected:
        raise ValueError("Behavioral seed validation replay is incomplete")
    return replay


def run_behavioral_optimization(
    config: OptimizationConfig,
    *,
    checker: Callable[..., Any] | None = None,
    proposer: Callable[..., Any] | None = None,
    optimize_fn: Callable[..., Any] = gepa.optimize,
) -> Any:
    config.run_dir.mkdir(parents=True, exist_ok=True)
    with _controller_lock(config):
        return _run_locked(
            config, checker=checker, proposer=proposer, optimize_fn=optimize_fn
        )


def _run_locked(
    config: OptimizationConfig,
    *,
    checker: Callable[..., Any] | None,
    proposer: Callable[..., Any] | None,
    optimize_fn: Callable[..., Any],
) -> Any:
    train, validation = load_behavioral_snapshot(config.dataset_snapshot)
    train_loader = BehavioralCaseLoader(train)
    validation_loader = BehavioralCaseLoader(validation)
    initial_rules = config.initial_rules_path.read_text(encoding="utf-8").strip()
    resuming = prepare_run_manifest(config, initial_rules=initial_rules)
    state = ReproducibleSearchState(config, resuming=resuming)
    audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
    _atomic_status(
        config.run_dir / "controller_status.json",
        {"schema_version": 1, "status": "running", "pid": os.getpid()},
    )
    class_counts = {
        split: {
            True: sum(case.accepted for case in cases),
            False: sum(not case.accepted for case in cases),
        }
        for split, cases in (("train", train), ("validation", validation))
    }
    audit.write(
        "behavioral_run_started",
        task_semantics=config.task.semantics,
        train_instances=len(train),
        validation_instances=len(validation),
        seed_candidate_sha256=text_sha256(initial_rules),
        max_metric_calls=config.search.max_metric_calls,
        max_iterations=config.search.max_iterations,
        reflection_minibatch_size=config.search.reflection_minibatch_size,
        class_counts_by_split=class_counts,
        resuming_from_state=resuming,
    )
    checker_runner = checker or BehavioralLocalChecker(config)
    batch_executor = (
        HPCSlurmBehavioralCheckerExecutor(config)
        if checker is None and config.execution.backend == "hpc_slurm"
        else None
    )
    if proposer is not None:
        proposer_runner = proposer
    elif config.execution.backend == "hpc_slurm":
        proposer_runner = HPCOfflineReflectionProposer(
            config,
            successful_proposals=state.successful_proposals,
            failures=state.reflection_failures,
        )
    else:
        proposer_runner = BehavioralLocalReflectionProposer(config)
        proposer_runner.successful_proposals = state.successful_proposals
        proposer_runner.failures = list(state.reflection_failures)
    if proposer is not None:
        proposer_runner.successful_proposals = state.successful_proposals
        proposer_runner.failures = list(state.reflection_failures)
    replay = (
        _seed_validation_replay(config, validation, initial_rules) if resuming else None
    )
    adapter = BehavioralGEPAAdapter(
        checker_runner,
        proposer=proposer_runner,
        run_dir=config.run_dir,
        primary_metric=config.search.primary_metric,
        class_counts_by_split=class_counts,
        train_case_repetitions=config.search.train_case_repetitions,
        batch_executor=batch_executor,
        startup_seed_replay=replay,
        seed_rules_sha256=text_sha256(initial_rules),
    )
    callback = ProgressCallback(
        config.run_dir,
        checkpoint=state,
        proposer=proposer_runner,
        accepted_candidates=state.accepted_candidates,
        completed_iterations=(
            max(0, GEPAState.load(str(config.run_dir)).i + 1)
            if (config.run_dir / "gepa_state.bin").is_file()
            else 0
        ),
    )
    stopper = MaxCandidateProposalsStopper(config.search.max_iterations)
    try:
        result = optimize_fn(
            seed_candidate={"rules": initial_rules},
            trainset=train_loader,
            valset=validation_loader,
            adapter=adapter,
            reflection_lm=None,
            candidate_selection_strategy=state.selector,
            frontier_type="instance",
            batch_sampler=state.sampler,
            reflection_minibatch_size=None,
            perfect_score=1.0,
            skip_perfect_score=config.search.skip_perfect_score,
            max_metric_calls=config.search.max_metric_calls,
            stop_callbacks=stopper,
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
        return None
    except (OfflineReflectionBlocked, Exception) as caught:
        exc = caught.cause if isinstance(caught, OfflineReflectionBlocked) else caught
        error = f"{type(exc).__name__}: {exc}"
        callback.mark_failed(phase="behavioral_optimization", error=error)
        JsonlLogger(config.run_dir / "errors.jsonl").write(
            "behavioral_optimization_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        _atomic_status(
            config.run_dir / "controller_status.json",
            {
                "schema_version": 1,
                "status": "failed",
                "failure_phase": "behavioral_optimization",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise
    write_behavioral_report(
        result,
        validation,
        config.run_dir,
        primary_metric=config.search.primary_metric,
    )
    successful = int(getattr(proposer_runner, "successful_proposals", 0))
    failures = list(getattr(proposer_runner, "failures", []))
    if successful < config.search.min_proposals:
        raise RuntimeError("Behavioral Reflection proposal threshold was not met")
    write_cost_report(
        config.run_dir,
        observed_metric_calls=int(result.total_metric_calls or 0),
        projection_metric_calls=config.search.projection_metric_calls,
        parallel=config.search.parallel,
        run_status="completed_with_warnings" if failures else "completed",
        successful_proposals=successful,
        required_proposals=config.search.min_proposals,
        reflection_failures=len(failures),
    )
    audit.write(
        "behavioral_run_completed",
        total_metric_calls=result.total_metric_calls,
        candidate_count=result.num_candidates,
        successful_proposals=successful,
    )
    _atomic_status(
        config.run_dir / "controller_status.json",
        {
            "schema_version": 1,
            "status": "completed_with_warnings" if failures else "completed",
        },
    )
    return result
