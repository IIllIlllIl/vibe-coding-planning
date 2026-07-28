"""Run GEPA search for online planning rules."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

import gepa
from gepa.core.adapter import EvaluationBatch
from gepa.core.state import GEPAState

from src.environment.docker_env import configure_docker_capacity
from src.exceptions import FatalError, OnlineControllerYield, SynthesisExhaustedError
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.online_adapter import OnlinePlanningGEPAAdapter
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.online_dataset import load_online_snapshot
from src.optimization.online_hpc_executor import HPCSlurmOnlineRolloutExecutor
from src.optimization.online_reflection import OnlinePlanningReflectionProposer
from src.optimization.online_rollout import OnlinePCTRolloutRunner
from src.optimization.report import write_cost_report


_BLOCKING_ERROR_MARKERS = (
    "checkpoint identity",
    "fingerprint mismatch",
    "infrastructure-invalid",
    "manifest differs",
    "out of memory",
    "out_of_memory",
    "quota exceeded",
    "no space left",
)


def _is_blocking_controller_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, (FatalError, MemoryError)):
            return True
        message = str(current).lower()
        if any(marker in message for marker in _BLOCKING_ERROR_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return isinstance(exc, ValueError)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class OnlineIterationProgressCallback:
    """Publish only GEPA iterations that are durable in the official state."""

    def __init__(self, run_dir: Path, completed_iterations: int) -> None:
        self.path = run_dir / "iteration_progress.json"
        self.first_observed = completed_iterations
        if self.path.is_file():
            existing = json.loads(self.path.read_text(encoding="utf-8"))
            self.first_observed = int(
                existing.get("first_observed_completed_iterations", completed_iterations)
            )
        self._save(completed_iterations, event="controller_started")

    def _save(self, completed_iterations: int, *, event: str) -> None:
        _atomic_json(
            self.path,
            {
                "schema_version": 1,
                "first_observed_completed_iterations": self.first_observed,
                "completed_iterations": completed_iterations,
                "last_event": event,
            },
        )

    def on_state_saved(self, event: dict[str, Any]) -> None:
        self._save(int(event["iteration"]), event="state_saved")

    def on_optimization_end(self, event: dict[str, Any]) -> None:
        self._save(int(event["total_iterations"]), event="optimization_completed")


@contextmanager
def _online_controller_lock(config: OnlineOptimizationConfig):
    """Prevent two online GEPA controllers from sharing one run_dir."""
    lock_path = config.run_dir / "online_controller.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "another online GEPA controller appears to be active for "
                f"run_dir {config.run_dir}; use a distinct run_dir or wait for "
                "the existing controller to finish"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_online_report(result: Any, config: OnlineOptimizationConfig) -> None:
    candidates = []
    for index, candidate in enumerate(result.candidates):
        scores = result.val_subscores[index]
        candidates.append(
            {
                "candidate_idx": index,
                "rules_sha256": text_sha256(candidate["rules"]),
                "validation_score": result.val_aggregate_scores[index],
                "validation_resolved_count": sum(
                    1 for score in scores.values() if score == 1.0
                ),
                "validation_rollout_count": len(scores),
                "parents": result.parents[index],
            }
        )
    (config.run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=list)
        + "\n",
        encoding="utf-8",
    )
    (config.run_dir / "candidate_metrics.json").write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (config.run_dir / "best_rules.txt").write_text(
        result.best_candidate["rules"] + "\n",
        encoding="utf-8",
    )
    (config.run_dir / "candidate_tree.html").write_text(
        result.candidate_tree_html(),
        encoding="utf-8",
    )


def _write_online_manifest(
    config: OnlineOptimizationConfig,
    *,
    initial_rules: str,
    train_ids: list[str],
    validation_ids: list[str],
) -> None:
    manifest = {
        "mode": "online_planning",
        "dataset_snapshot": str(config.dataset_snapshot),
        "train_instance_ids": train_ids,
        "validation_instance_ids": validation_ids,
        "initial_rules_sha256": text_sha256(initial_rules),
        "run_dir": str(config.run_dir),
        "candidate_components": ["rules"],
        "input_boundary": {
            "plan_agent": ["issue_description", "candidate_rules", "repository"],
            "code_agent": ["issue_description", "generated_plan", "repository"],
            "reflection": [
                "candidate_rules",
                "issue_description",
                "repository_identity",
                "generated_plan",
                "plan_trajectory",
                "code_trajectory",
                "generated_patch",
                "evaluator_result",
                "current_rollout_resolved",
                "score",
                "instance_reflection_review",
            ],
            "historical_plan_used": False,
            "historical_resolved_used": False,
            "historical_asi_used": False,
        },
        "models": {
            "plan": config.plan.model,
            "code": config.code.model,
            "reflection": config.reflection.model,
            "reflection_reviewer": config.reflection.model,
        },
        "search": {
            "max_metric_calls": config.search.max_metric_calls,
            "reflection_minibatch_size": config.search.reflection_minibatch_size,
            "parallel": config.search.parallel,
            "seed": config.search.seed,
        },
    }
    path = config.run_dir / "online_run_manifest.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("mode") != "online_planning":
            raise ValueError("run_dir contains a non-online GEPA manifest")
        immutable_keys = (
            "dataset_snapshot",
            "initial_rules_sha256",
            "candidate_components",
            "input_boundary",
            "models",
        )
        for key in immutable_keys:
            if existing.get(key) != manifest.get(key):
                raise ValueError(f"online GEPA manifest differs at {key}")
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_resume_seed_evaluation(
    config: OnlineOptimizationConfig,
    *,
    initial_rules: str,
    validation: list[Any],
) -> EvaluationBatch | None:
    """Return the persisted seed scores GEPA otherwise recomputes before load."""
    state_path = config.run_dir / "gepa_state.bin"
    if not state_path.is_file():
        return None
    state = GEPAState.load(str(config.run_dir))
    if not state.program_candidates or state.program_candidates[0] != {
        "rules": initial_rules
    }:
        raise ValueError("online GEPA checkpoint seed candidate differs from config")
    scores_by_id = state.prog_candidate_val_subscores[0]
    expected_ids = list(range(len(validation)))
    if set(scores_by_id) != set(expected_ids):
        raise ValueError(
            "online GEPA checkpoint validation IDs differ from the current snapshot"
        )

    # GEPA discards this pre-load EvaluationBatch once it loads gepa_state.bin.
    # Preserve real seed outputs when available and use a minimal marker otherwise.
    outputs = []
    best_outputs = state.best_outputs_valset or {}
    for val_id, case in enumerate(validation):
        seed_output = next(
            (
                output
                for program_idx, output in best_outputs.get(val_id, [])
                if program_idx == 0
            ),
            None,
        )
        outputs.append(
            seed_output
            if seed_output is not None
            else {
                "instance_id": case.instance_id,
                "resolved": bool(scores_by_id[val_id]),
                "checkpoint_restored": True,
            }
        )
    return EvaluationBatch(
        outputs=outputs,
        scores=[float(scores_by_id[val_id]) for val_id in expected_ids],
        trajectories=None,
    )


def run_online_optimization(
    config: OnlineOptimizationConfig,
    *,
    rollout: Callable[..., Any] | None = None,
    proposer: Callable[..., Any] | None = None,
    optimize_fn: Callable[..., Any] = gepa.optimize,
) -> Any:
    train, validation = load_online_snapshot(config.dataset_snapshot)
    if config.dataset.train_instance_ids:
        allowed = set(config.dataset.train_instance_ids)
        train = [case for case in train if case.instance_id in allowed]
        if {case.instance_id for case in train} != allowed:
            missing = sorted(allowed - {case.instance_id for case in train})
            raise ValueError(f"online train instance IDs not found: {missing}")
    if config.dataset.validation_instance_ids:
        allowed = set(config.dataset.validation_instance_ids)
        validation = [case for case in validation if case.instance_id in allowed]
        if {case.instance_id for case in validation} != allowed:
            missing = sorted(allowed - {case.instance_id for case in validation})
            raise ValueError(
                f"online validation instance IDs not found: {missing}"
            )
    if not train or not validation:
        raise ValueError("online GEPA requires non-empty train and validation sets")
    config.run_dir.mkdir(parents=True, exist_ok=True)
    with _online_controller_lock(config):
        return _run_online_optimization_locked(
            config,
            train=train,
            validation=validation,
            rollout=rollout,
            proposer=proposer,
            optimize_fn=optimize_fn,
        )


def _run_online_optimization_locked(
    config: OnlineOptimizationConfig,
    *,
    train: list[Any],
    validation: list[Any],
    rollout: Callable[..., Any] | None,
    proposer: Callable[..., Any] | None,
    optimize_fn: Callable[..., Any],
) -> Any:
    initial_rules = config.initial_rules_path.read_text(encoding="utf-8").strip()
    _write_online_manifest(
        config,
        initial_rules=initial_rules,
        train_ids=[case.instance_id for case in train],
        validation_ids=[case.instance_id for case in validation],
    )
    audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
    _atomic_json(
        config.run_dir / "controller_status.json",
        {"schema_version": 1, "status": "running", "pid": os.getpid()},
    )
    audit.write(
        "online_run_started",
        seed_candidate_sha256=text_sha256(initial_rules),
        train_instances=len(train),
        validation_instances=len(validation),
        max_metric_calls=config.search.max_metric_calls,
        reflection_minibatch_size=config.search.reflection_minibatch_size,
        parallel=config.search.parallel,
        seed=config.search.seed,
        candidate_components=["rules"],
        plan_agent_receives_candidate_rules=True,
        code_agent_receives_candidate_rules=False,
        historical_plan_used=False,
        historical_resolved_used=False,
        historical_asi_used=False,
    )
    capacity = configure_docker_capacity(
        config.docker,
        max_concurrent=config.search.parallel,
        enable_docker_maintenance=config.container.runtime == "docker",
    )
    rollout_runner = rollout or OnlinePCTRolloutRunner(config, capacity)
    batch_executor = None
    if rollout is None and config.execution.backend == "hpc_slurm":
        batch_executor = HPCSlurmOnlineRolloutExecutor(config)
    proposer_runner = proposer or OnlinePlanningReflectionProposer(
        config,
        capacity,
    )
    resume_seed_evaluation = _load_resume_seed_evaluation(
        config,
        initial_rules=initial_rules,
        validation=validation,
    )
    completed_iterations = 0
    if (config.run_dir / "gepa_state.bin").is_file():
        completed_iterations = max(0, GEPAState.load(str(config.run_dir)).i + 1)
    progress_callback = OnlineIterationProgressCallback(
        config.run_dir,
        completed_iterations,
    )
    adapter = OnlinePlanningGEPAAdapter(
        rollout_runner,
        parallel=config.search.parallel,
        proposer=proposer_runner,
        run_dir=config.run_dir,
        batch_executor=batch_executor,
        resume_seed_evaluation=resume_seed_evaluation,
        resume_seed_key=(
            (
                text_sha256(initial_rules),
                tuple(case.instance_id for case in validation),
            )
            if resume_seed_evaluation is not None
            else None
        ),
    )
    reflection_failure_count_before = len(getattr(proposer_runner, "failures", []))
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
            callbacks=[progress_callback],
            seed=config.search.seed,
        )
    except OnlineControllerYield as exc:
        _atomic_json(
            config.run_dir / "controller_yield.json",
            {
                "schema_version": 1,
                "status": "yielded",
                "reason": exc.reason,
                "batch_dir": exc.batch_dir,
                "worker_job_id": exc.job_id,
                "completed_iterations": completed_iterations,
            },
        )
        _atomic_json(
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
            "online_controller_yielded",
            reason=exc.reason,
            batch_dir=exc.batch_dir,
            worker_job_id=exc.job_id,
        )
        return None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        reflection_failures = list(getattr(proposer_runner, "failures", []))
        new_reflection_failure = (
            len(reflection_failures) > reflection_failure_count_before
        )
        blocking_failure = _is_blocking_controller_error(exc)
        controller_status = "failed" if blocking_failure else "retryable_failed"
        JsonlLogger(config.run_dir / "errors.jsonl").write(
            "online_optimization_failed",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        write_cost_report(
            config.run_dir,
            observed_metric_calls=0,
            projection_metric_calls=config.search.projection_metric_calls,
            parallel=config.search.parallel,
            run_status="failed",
            successful_proposals=int(
                getattr(proposer_runner, "successful_proposals", 0)
            ),
            required_proposals=config.search.min_proposals,
            reflection_failures=len(reflection_failures),
        )
        audit.write("online_run_failed", error=error)
        _atomic_json(
            config.run_dir / "controller_status.json",
            {
                "schema_version": 1,
                "status": controller_status,
                "failure_phase": (
                    "synthesis"
                    if isinstance(exc, SynthesisExhaustedError)
                    else "reflection"
                    if new_reflection_failure
                    else "controller"
                ),
                "blocking": blocking_failure,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    _write_online_report(result, config)
    write_cost_report(
        config.run_dir,
        observed_metric_calls=int(result.total_metric_calls or 0),
        projection_metric_calls=config.search.projection_metric_calls,
        parallel=config.search.parallel,
        successful_proposals=int(getattr(proposer_runner, "successful_proposals", 0)),
        required_proposals=config.search.min_proposals,
        reflection_failures=len(getattr(proposer_runner, "failures", [])),
    )
    audit.write(
        "online_run_completed",
        best_candidate_idx=result.best_idx,
        best_candidate_sha256=text_sha256(result.best_candidate["rules"]),
        total_metric_calls=result.total_metric_calls,
        candidate_count=result.num_candidates,
    )
    _atomic_json(
        config.run_dir / "controller_status.json",
        {"schema_version": 1, "status": "completed"},
    )
    return result
