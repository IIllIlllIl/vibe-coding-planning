"""GEPA callbacks for atomic progress and error reporting."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.optimization.audit import JsonlLogger, text_sha256


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class ProgressCallback:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.audit = JsonlLogger(run_dir / "audit_events.jsonl")
        self.valset_size = 0
        self.progress: dict[str, Any] = {
            "status": "starting",
            "iteration": 0,
            "metric_calls_used": 0,
            "accepted_candidates": 0,
        }

    def _save(self) -> None:
        _atomic_json(self.run_dir / "progress.json", self.progress)

    def on_optimization_start(self, event: dict[str, Any]) -> None:
        self.valset_size = event["valset_size"]
        self.progress.update(
            status="running",
            trainset_size=event["trainset_size"],
            valset_size=event["valset_size"],
        )
        self.audit.write(
            "gepa_optimization_started",
            trainset_size=event["trainset_size"],
            valset_size=event["valset_size"],
            seed_candidate_sha256=text_sha256(
                event["seed_candidate"]["rules"]
            ),
            seed_rules_empty=event["seed_candidate"]["rules"] == "",
        )
        self._save()

    def on_iteration_start(self, event: dict[str, Any]) -> None:
        self.progress["iteration"] = event["iteration"]
        self.audit.write("gepa_iteration_started", iteration=event["iteration"])
        self._save()

    def on_evaluation_start(self, event: dict[str, Any]) -> None:
        inputs = event.get("inputs", [])
        instance_ids = [
            getattr(item, "instance_id", None)
            or (item.get("instance_id") if isinstance(item, dict) else None)
            for item in inputs
        ]
        evaluation_kind = "minibatch"
        if event["is_seed_candidate"] and event["batch_size"] == self.valset_size:
            evaluation_kind = "baseline_validation"
        elif event["batch_size"] == self.valset_size:
            evaluation_kind = "full_validation"
        self.audit.write(
            "gepa_evaluation_started",
            iteration=event["iteration"],
            candidate_idx=event["candidate_idx"],
            batch_size=event["batch_size"],
            capture_traces=event["capture_traces"],
            is_seed_candidate=event["is_seed_candidate"],
            evaluation_kind=evaluation_kind,
            instance_ids=instance_ids,
        )

    def on_evaluation_end(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_evaluation_completed",
            iteration=event["iteration"],
            candidate_idx=event["candidate_idx"],
            scores=event["scores"],
            has_trajectories=event["has_trajectories"],
            is_seed_candidate=event["is_seed_candidate"],
        )

    def on_evaluation_skipped(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_evaluation_skipped",
            iteration=event["iteration"],
            candidate_idx=event["candidate_idx"],
            reason=event["reason"],
            is_seed_candidate=event["is_seed_candidate"],
        )

    def on_minibatch_sampled(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_minibatch_sampled",
            iteration=event["iteration"],
            minibatch_ids=event["minibatch_ids"],
            trainset_size=event["trainset_size"],
        )

    def on_proposal_start(self, event: dict[str, Any]) -> None:
        parent = event["parent_candidate"]["rules"]
        self.audit.write(
            "gepa_proposal_started",
            iteration=event["iteration"],
            parent_candidate_sha256=text_sha256(parent),
            parent_rules_empty=parent == "",
            components=event["components"],
        )

    def on_proposal_end(self, event: dict[str, Any]) -> None:
        proposed = event["new_instructions"]["rules"]
        self.audit.write(
            "gepa_proposal_completed",
            iteration=event["iteration"],
            proposed_candidate_sha256=text_sha256(proposed),
            proposed_rules_empty=proposed == "",
            components=sorted(event["new_instructions"]),
        )

    def on_candidate_accepted(self, event: dict[str, Any]) -> None:
        self.progress["accepted_candidates"] += 1
        self.progress["latest_candidate_idx"] = event["new_candidate_idx"]
        self.progress["latest_score"] = event["new_score"]
        self.audit.write(
            "gepa_candidate_accepted",
            iteration=event["iteration"],
            new_candidate_idx=event["new_candidate_idx"],
            minibatch_score=event["new_score"],
            parent_ids=event["parent_ids"],
            full_validation_completed_before_acceptance=True,
        )
        self._save()

    def on_candidate_rejected(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_candidate_rejected",
            iteration=event["iteration"],
            old_minibatch_score=event["old_score"],
            new_minibatch_score=event["new_score"],
            reason=event["reason"],
            full_validation_skipped=True,
        )

    def on_valset_evaluated(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_validation_completed",
            iteration=event["iteration"],
            candidate_idx=event["candidate_idx"],
            average_score=event["average_score"],
            num_examples_evaluated=event["num_examples_evaluated"],
            total_valset_size=event["total_valset_size"],
            is_best_program=event["is_best_program"],
            full_validation=(
                event["num_examples_evaluated"] == event["total_valset_size"]
            ),
        )

    def on_budget_updated(self, event: dict[str, Any]) -> None:
        self.progress["metric_calls_used"] = event["metric_calls_used"]
        self.progress["metric_calls_remaining"] = event["metric_calls_remaining"]
        self.audit.write(
            "gepa_budget_updated",
            iteration=event["iteration"],
            metric_calls_used=event["metric_calls_used"],
            metric_calls_delta=event["metric_calls_delta"],
            metric_calls_remaining=event["metric_calls_remaining"],
        )
        self._save()

    def on_state_saved(self, event: dict[str, Any]) -> None:
        self.audit.write(
            "gepa_state_saved",
            iteration=event["iteration"],
            run_dir=event["run_dir"],
            state_file="gepa_state.bin",
        )

    def on_error(self, event: dict[str, Any]) -> None:
        JsonlLogger(self.run_dir / "errors.jsonl").write(
            "gepa_error",
            iteration=event["iteration"],
            error=repr(event["exception"]),
            will_continue=event["will_continue"],
        )

    def mark_failed(self, *, phase: str, error: str) -> None:
        self.progress.update(
            status="failed",
            failure_phase=phase,
            failure=error,
        )
        self.audit.write(
            "gepa_optimization_failed",
            phase=phase,
            error=error,
        )
        self._save()

    def on_optimization_end(self, event: dict[str, Any]) -> None:
        self.progress.update(
            status="completed",
            best_candidate_idx=event["best_candidate_idx"],
            total_iterations=event["total_iterations"],
            metric_calls_used=event["total_metric_calls"],
        )
        self.audit.write(
            "gepa_optimization_completed",
            best_candidate_idx=event["best_candidate_idx"],
            total_iterations=event["total_iterations"],
            total_metric_calls=event["total_metric_calls"],
        )
        self._save()
