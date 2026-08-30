"""GEPA adapter for Behavioral Plan Acceptability decisions."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from gepa.core.adapter import EvaluationBatch

from src.optimization.behavioral_models import (
    BehavioralCheckerOutput,
    BehavioralGEPACase,
)


BehavioralCheckerRunner = Callable[[BehavioralGEPACase, str], BehavioralCheckerOutput]


class BehavioralGEPAAdapter:
    """Map explicit acceptability predictions to GEPA's scalar score API."""

    def __init__(
        self,
        checker: BehavioralCheckerRunner,
        *,
        proposer: Any = None,
        run_dir: Path | None = None,
        primary_metric: str = "accuracy",
        class_counts_by_split: Mapping[str, Mapping[bool, int]] | None = None,
        train_case_repetitions: int = 1,
    ) -> None:
        if primary_metric not in ("accuracy", "balanced_accuracy"):
            raise ValueError("unsupported Behavioral primary metric")
        if train_case_repetitions < 1:
            raise ValueError("train_case_repetitions must be positive")
        self.checker = checker
        self.propose_new_texts = proposer
        self.run_dir = run_dir
        self.primary_metric = primary_metric
        self.train_case_repetitions = train_case_repetitions
        self.class_counts_by_split = {
            split: {bool(label): int(count) for label, count in counts.items()}
            for split, counts in (class_counts_by_split or {}).items()
        }
        if primary_metric == "balanced_accuracy":
            for split in ("train", "validation"):
                counts = self.class_counts_by_split.get(split, {})
                if counts.get(True, 0) < 1 or counts.get(False, 0) < 1:
                    raise ValueError(
                        "balanced_accuracy requires ACCEPT and DO_NOT_ACCEPT "
                        f"cases in {split}"
                    )

    def _score(self, case: BehavioralGEPACase, predicted_accept: bool) -> float:
        if predicted_accept != case.accepted:
            return 0.0
        if self.primary_metric == "accuracy":
            return 1.0
        counts = self.class_counts_by_split[case.split]
        return (counts[True] + counts[False]) / (2 * counts[case.accepted])

    def _evaluate_physical(
        self, case: BehavioralGEPACase, guideline: str, capture_traces: bool
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        output = self.checker(case, guideline)
        score = self._score(case, output.predicted_accept)
        public_output = {"instance_id": case.instance_id, **output.to_dict()}
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "task_semantics": "behavioral_plan_acceptability_v1",
                "observed_decision": case.decision,
                "observed_accept": case.accepted,
                "score": score,
                "primary_metric": self.primary_metric,
                "is_correct": output.predicted_accept == case.accepted,
                "checker_output": output.to_dict(include_trajectory=True),
                "reflection_evidence": case.reflection_evidence,
                "repository_proxy_provenance": (
                    case.reflection_repository_provenance()
                ),
            }
        return public_output, score, trace

    def evaluate(
        self,
        batch: list[BehavioralGEPACase],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        if set(candidate) != {"rules"} or not isinstance(candidate["rules"], str):
            raise ValueError("candidate must contain only the string component rules")
        if len({case.split for case in batch}) > 1:
            raise ValueError("Behavioral GEPA evaluation batches may not mix splits")
        repetitions = (
            self.train_case_repetitions if batch and batch[0].split == "train" else 1
        )
        rows: list[tuple[dict[str, Any], float, dict[str, Any] | None]] = []
        for case in batch:
            physical = [
                replace(case, repetition_index=index) for index in range(repetitions)
            ]
            physical_rows = [
                self._evaluate_physical(item, candidate["rules"], capture_traces)
                for item in physical
            ]
            if repetitions == 1:
                rows.append(physical_rows[0])
                continue
            score = sum(item[1] for item in physical_rows) / repetitions
            public_output = {
                "instance_id": case.instance_id,
                "status": "repeated_checker_aggregate",
                "repetition_count": repetitions,
                "score": score,
                "repetitions": [item[0] for item in physical_rows],
            }
            trace = None
            if capture_traces:
                trace = {
                    "instance_id": case.instance_id,
                    "task_semantics": "behavioral_plan_acceptability_v1",
                    "observed_decision": case.decision,
                    "observed_accept": case.accepted,
                    "score": score,
                    "primary_metric": self.primary_metric,
                    "repetition_count": repetitions,
                    "checker_output": public_output,
                    "reflection_evidence": case.reflection_evidence,
                    "repository_proxy_provenance": (
                        case.reflection_repository_provenance()
                    ),
                }
            rows.append((public_output, score, trace))

        result = EvaluationBatch(
            outputs=[item[0] for item in rows],
            scores=[item[1] for item in rows],
            trajectories=[item[2] for item in rows] if capture_traces else None,
        )
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / "behavioral_evaluations.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                for case, output, score in zip(
                    batch, result.outputs, result.scores, strict=True
                ):
                    handle.write(
                        json.dumps(
                            {
                                "instance_id": case.instance_id,
                                "split": case.split,
                                "observed_decision": case.decision,
                                "score": score,
                                "output": output,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
        return result

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if components_to_update != ["rules"]:
            raise ValueError("only rules may be optimized")
        if eval_batch.trajectories is None:
            raise ValueError("reflection requires captured trajectories")
        return {"rules": eval_batch.trajectories}
