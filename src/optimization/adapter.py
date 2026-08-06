"""GEPA adapter for fixed Checker evaluation."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence
import uuid

from gepa.core.adapter import EvaluationBatch

from src.optimization.audit import JsonlLogger, redact_sensitive, text_sha256
from src.optimization.checker import (
    CheckerAgentTimeout,
    CheckerOutputContractError,
    CheckerRunner,
    checker_retry_feedback,
)
from src.optimization.models import (
    CheckerResult,
    CheckerTimeoutOutput,
    GEPACase,
)


def _exception_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "error": str(exc),
    }
    for attr in ("cmd", "returncode", "timeout"):
        if hasattr(exc, attr):
            details[attr] = getattr(exc, attr)
    for attr in ("stdout", "stderr", "output"):
        value = getattr(exc, attr, None)
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if value:
            details[attr] = str(value)[-4000:]
    return details


class CheckerBatchExecutor(Protocol):
    def evaluate(
        self,
        batch: list[GEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[CheckerResult]: ...


class CheckerGEPAAdapter:
    def __init__(
        self,
        checker: CheckerRunner,
        *,
        parallel: int = 1,
        proposer: Any = None,
        run_dir: Path | None = None,
        checker_attempts: int = 1,
        startup_seed_replay: dict[
            str, tuple[dict[str, Any], float]
        ] | None = None,
        seed_rules_sha256: str | None = None,
        primary_metric: str = "accuracy",
        class_counts_by_split: Mapping[str, Mapping[bool, int]] | None = None,
        batch_executor: CheckerBatchExecutor | None = None,
    ) -> None:
        self.checker = checker
        self.parallel = parallel
        self.propose_new_texts = proposer
        self.run_dir = run_dir
        self.checker_attempts = checker_attempts
        self.startup_seed_replay = startup_seed_replay
        self.seed_rules_sha256 = seed_rules_sha256
        self.batch_executor = batch_executor
        if primary_metric not in ("accuracy", "balanced_accuracy"):
            raise ValueError(
                "primary_metric must be 'accuracy' or 'balanced_accuracy'"
            )
        self.primary_metric = primary_metric
        self.class_counts_by_split = {
            split: {bool(label): int(count) for label, count in counts.items()}
            for split, counts in (class_counts_by_split or {}).items()
        }
        if self.primary_metric == "balanced_accuracy":
            for split in ("train", "validation"):
                counts = self.class_counts_by_split.get(split, {})
                if counts.get(True, 0) < 1 or counts.get(False, 0) < 1:
                    raise ValueError(
                        "balanced_accuracy requires positive resolved and "
                        f"unresolved class counts for {split}"
                    )
        self.audit = (
            JsonlLogger(run_dir / "audit_events.jsonl")
            if run_dir is not None
            else None
        )
        self.errors = (
            JsonlLogger(run_dir / "errors.jsonl")
            if run_dir is not None
            else None
        )

    def _score_details(
        self,
        case: GEPACase,
        predicted_resolved: bool,
    ) -> dict[str, Any]:
        is_correct = predicted_resolved == case.resolved
        if predicted_resolved:
            classification_outcome = (
                "true_positive" if case.resolved else "false_positive"
            )
        else:
            classification_outcome = (
                "false_negative" if case.resolved else "true_negative"
            )
        class_weight = 1.0
        if self.primary_metric == "balanced_accuracy":
            counts = self.class_counts_by_split[case.split]
            total = counts[True] + counts[False]
            class_weight = total / (2 * counts[case.resolved])
        return {
            "primary_metric": self.primary_metric,
            "is_correct": is_correct,
            "classification_outcome": classification_outcome,
            "error_type": None if is_correct else classification_outcome,
            "class_weight": class_weight,
            "score": class_weight if is_correct else 0.0,
        }

    def _timeout_score_details(self, case: GEPACase) -> dict[str, Any]:
        class_weight = 1.0
        if self.primary_metric == "balanced_accuracy":
            counts = self.class_counts_by_split[case.split]
            total = counts[True] + counts[False]
            class_weight = total / (2 * counts[case.resolved])
        return {
            "primary_metric": self.primary_metric,
            "is_correct": False,
            "classification_outcome": "checker_timeout",
            "error_type": "checker_timeout",
            "class_weight": class_weight,
            "score": 0.0,
        }

    def _timeout_row(
        self,
        case: GEPACase,
        output: CheckerTimeoutOutput,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        score_details = self._timeout_score_details(case)
        public_output = {
            "instance_id": case.instance_id,
            **output.to_dict(),
        }
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "expected_resolved": case.resolved,
                **score_details,
                "checker_output": output.to_dict(include_trajectory=True),
                **case.asi,
            }
        return public_output, 0.0, trace

    def _save_checker_trajectory(
        self,
        *,
        case: GEPACase,
        rules: str,
        attempt: int,
        status: str,
        messages: Sequence[Mapping[str, Any]] = (),
        output: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
        retry_feedback: str = "",
    ) -> Path | None:
        if self.run_dir is None:
            return None
        candidate_sha256 = text_sha256(rules)
        call_id = uuid.uuid4().hex
        root = (
            self.run_dir
            / "checker_trajectories"
            / candidate_sha256[:12]
            / case.instance_id
        )
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{call_id}.json"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "call_id": call_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "instance_id": case.instance_id,
            "split": case.split,
            "candidate_sha256": candidate_sha256,
            "attempt": attempt,
            "max_attempts": self.checker_attempts,
            "status": status,
            "messages": list(messages),
            "retry_feedback": retry_feedback,
        }
        if output is not None:
            payload["output"] = dict(output)
        if error is not None:
            payload.update(_exception_details(error))
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                redact_sensitive(payload),
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        if self.audit is not None:
            self.audit.write(
                "checker_trajectory_saved",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                attempt=attempt,
                status=status,
                trajectory_path=str(path),
            )
        return path

    def _evaluate_one(
        self,
        case: GEPACase,
        rules: str,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        last_exc: BaseException | None = None
        timeout_trajectories: list[tuple[dict[str, Any], ...]] = []
        retry_feedback = ""
        for attempt in range(1, self.checker_attempts + 1):
            try:
                return self._evaluate_one_attempt(
                    case,
                    rules,
                    capture_traces,
                    attempt=attempt,
                    retry_feedback=retry_feedback,
                )
            except (CheckerAgentTimeout, Exception) as exc:
                last_exc = exc
                if isinstance(exc, CheckerAgentTimeout):
                    timeout_trajectories.append(
                        tuple(getattr(exc, "checker_trajectory", ()))
                    )
                retry_feedback = (
                    checker_retry_feedback(str(exc))
                    if isinstance(exc, CheckerOutputContractError)
                    else ""
                )
                details = _exception_details(exc)
                if self.audit is not None:
                    self.audit.write(
                        "checker_evaluation_attempt_failed",
                        instance_id=case.instance_id,
                        candidate_sha256=text_sha256(rules),
                        attempt=attempt,
                        max_attempts=self.checker_attempts,
                        **details,
                    )
        assert last_exc is not None
        if (
            isinstance(last_exc, CheckerAgentTimeout)
            and len(timeout_trajectories) == self.checker_attempts
        ):
            return self._timeout_row(
                case,
                CheckerTimeoutOutput(
                    attempts=self.checker_attempts,
                    timeout_seconds=int(
                        getattr(last_exc, "timeout_seconds", 0)
                    ),
                    trajectories=tuple(timeout_trajectories),
                ),
                capture_traces,
            )
        details = _exception_details(last_exc)
        if self.audit is not None:
            self.audit.write(
                "checker_evaluation_failed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                attempts=self.checker_attempts,
                **details,
            )
        if self.errors is not None:
            self.errors.write(
                "checker_evaluation_failed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                attempts=self.checker_attempts,
                **details,
            )
        output = {
            "instance_id": case.instance_id,
            "error": f"{type(last_exc).__name__}: {last_exc}",
        }
        trace = (
            {
                "instance_id": case.instance_id,
                "expected_resolved": case.resolved,
                "score": 0.0,
                "checker_output": output,
                **case.asi,
            }
            if capture_traces
            else None
        )
        return output, 0.0, trace

    def _evaluate_one_attempt(
        self,
        case: GEPACase,
        rules: str,
        capture_traces: bool,
        *,
        attempt: int,
        retry_feedback: str = "",
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        try:
            prepare = getattr(self.checker, "prepare", None)
            if callable(prepare):
                prepare(case)
            if retry_feedback:
                output = self.checker(
                    case,
                    rules,
                    retry_feedback=retry_feedback,
                )
            else:
                output = self.checker(case, rules)
        except Exception as exc:
            self._save_checker_trajectory(
                case=case,
                rules=rules,
                attempt=attempt,
                status="failed",
                messages=getattr(exc, "checker_trajectory", ()),
                error=exc,
                retry_feedback=retry_feedback,
            )
            raise
        self._save_checker_trajectory(
            case=case,
            rules=rules,
            attempt=attempt,
            status="completed",
            messages=output.trajectory,
            output=output.to_dict(),
            retry_feedback=retry_feedback,
        )
        if self.audit is not None and attempt > 1:
            self.audit.write(
                "checker_evaluation_retried",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                successful_attempt=attempt,
                max_attempts=self.checker_attempts,
            )
        score_details = self._score_details(
            case,
            output.predicted_resolved,
        )
        score = float(score_details["score"])
        public_output = {
            "instance_id": case.instance_id,
            **output.to_dict(),
        }
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "expected_resolved": case.resolved,
                **score_details,
                "checker_output": output.to_dict(include_trajectory=True),
                **case.asi,
            }
        return public_output, score, trace

    def _row_from_checker_output(
        self,
        case: GEPACase,
        output: CheckerResult,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        if isinstance(output, CheckerTimeoutOutput):
            return self._timeout_row(case, output, capture_traces)
        score_details = self._score_details(case, output.predicted_resolved)
        public_output = {
            "instance_id": case.instance_id,
            **output.to_dict(),
        }
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "expected_resolved": case.resolved,
                **score_details,
                "checker_output": output.to_dict(include_trajectory=True),
                **case.asi,
            }
        return public_output, float(score_details["score"]), trace

    def _evaluate_parallel_fail_fast(
        self,
        batch: list[GEPACase],
        rules: str,
        capture_traces: bool,
        candidate_hash: str,
    ) -> list[tuple[dict[str, Any], float, dict[str, Any] | None]]:
        rows: list[tuple[dict[str, Any], float, dict[str, Any] | None] | None] = [
            None
        ] * len(batch)
        active: dict[
            Future[tuple[dict[str, Any], float, dict[str, Any] | None]], int
        ] = {}
        next_index = 0

        with ThreadPoolExecutor(max_workers=self.parallel) as executor:
            while next_index < len(batch) and len(active) < self.parallel:
                active[
                    executor.submit(
                        self._evaluate_one,
                        batch[next_index],
                        rules,
                        capture_traces,
                    )
                ] = next_index
                next_index += 1

            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    index = active.pop(future)
                    row = future.result()
                    rows[index] = row
                    output = row[0]
                    if "error" in output:
                        for pending in active:
                            pending.cancel()
                        completed = sum(row is not None for row in rows)
                        if self.audit is not None:
                            self.audit.write(
                                "adapter_evaluation_aborted",
                                candidate_sha256=candidate_hash,
                                instance_id=str(output["instance_id"]),
                                completed=completed,
                                in_flight=len(active),
                                not_started=len(batch)
                                - completed
                                - len(active),
                                reason="checker_operational_failure",
                            )
                        raise RuntimeError(
                            "Checker operational failure for: "
                            + str(output["instance_id"])
                        )
                    if next_index < len(batch):
                        active[
                            executor.submit(
                                self._evaluate_one,
                                batch[next_index],
                                rules,
                                capture_traces,
                            )
                        ] = next_index
                        next_index += 1

        return [row for row in rows if row is not None]

    def evaluate(
        self,
        batch: list[GEPACase],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        if set(candidate) != {"rules"} or not isinstance(candidate["rules"], str):
            raise ValueError("candidate must contain only the string component rules")
        candidate_hash = text_sha256(candidate["rules"])
        if (
            self.startup_seed_replay is not None
            and candidate_hash == self.seed_rules_sha256
            and not capture_traces
            and {case.instance_id for case in batch}
            == set(self.startup_seed_replay)
        ):
            rows = [self.startup_seed_replay[case.instance_id] for case in batch]
            self.startup_seed_replay = None
            if self.audit is not None:
                self.audit.write(
                    "seed_validation_replayed",
                    candidate_sha256=candidate_hash,
                    instance_ids=[case.instance_id for case in batch],
                    checker_calls_avoided=len(batch),
                )
            return EvaluationBatch(
                outputs=[row[0] for row in rows],
                scores=[row[1] for row in rows],
                trajectories=None,
            )
        if self.audit is not None:
            self.audit.write(
                "adapter_evaluation_started",
                candidate_sha256=candidate_hash,
                candidate_rules_empty=candidate["rules"] == "",
                instance_ids=[case.instance_id for case in batch],
                split_values=sorted({case.split for case in batch}),
                capture_traces=capture_traces,
                checker_visible_fields=[
                    "issue_description",
                    "plan",
                    "repository",
                    "candidate_rules",
                ],
                checker_forbidden_fields=[
                    "resolved",
                    "plan_trajectory",
                    "code_trajectory",
                    "generated_patch",
                    "evaluator_result",
                ],
                retry_feedback_policy=(
                    "previous_output_validator_error_only"
                ),
                parallel=self.parallel,
                checker_attempts=self.checker_attempts,
                primary_metric=self.primary_metric,
                class_counts_by_split=self.class_counts_by_split,
            )
        if self.batch_executor is not None:
            outputs = self.batch_executor.evaluate(
                batch,
                candidate["rules"],
                capture_traces,
            )
            if len(outputs) != len(batch):
                raise RuntimeError(
                    "Offline Checker batch output count does not match input"
                )
            rows = [
                self._row_from_checker_output(case, output, capture_traces)
                for case, output in zip(batch, outputs, strict=True)
            ]
        elif self.parallel > 1:
            rows = self._evaluate_parallel_fail_fast(
                batch,
                candidate["rules"],
                capture_traces,
                candidate_hash,
            )
        else:
            rows = [
                self._evaluate_one(case, candidate["rules"], capture_traces)
                for case in batch
            ]
        errors = [output for output, _, _ in rows if "error" in output]
        if errors:
            instance_ids = [str(output["instance_id"]) for output in errors]
            raise RuntimeError(
                "Checker operational failure for: "
                + ", ".join(instance_ids)
            )
        result = EvaluationBatch(
            outputs=[row[0] for row in rows],
            scores=[row[1] for row in rows],
            trajectories=[row[2] for row in rows] if capture_traces else None,
        )
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            candidate_hash = hashlib.sha256(
                candidate["rules"].encode("utf-8")
            ).hexdigest()
            with (self.run_dir / "evaluations.jsonl").open(
                "a",
                encoding="utf-8",
            ) as handle:
                for case, output, score in zip(
                    batch,
                    result.outputs,
                    result.scores,
                    strict=True,
                ):
                    score_details = (
                        self._timeout_score_details(case)
                        if output.get("status") == "timeout"
                        else self._score_details(
                            case,
                            bool(output["predicted_resolved"]),
                        )
                    )
                    handle.write(
                        json.dumps(
                            {
                                "candidate_sha256": candidate_hash,
                                "instance_id": case.instance_id,
                                "split": case.split,
                                "resolved": case.resolved,
                                "score": score,
                                "primary_metric": self.primary_metric,
                                "is_correct": score_details["is_correct"],
                                "classification_outcome": score_details[
                                    "classification_outcome"
                                ],
                                "error_type": score_details["error_type"],
                                "class_weight": score_details["class_weight"],
                                "output": output,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
        if self.audit is not None:
            self.audit.write(
                "adapter_evaluation_completed",
                candidate_sha256=candidate_hash,
                instance_ids=[case.instance_id for case in batch],
                scores=result.scores,
                capture_traces=capture_traces,
                error_count=sum("error" in output for output in result.outputs),
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
        if self.audit is not None:
            self.audit.write(
                "reflective_dataset_created",
                candidate_sha256=text_sha256(candidate["rules"]),
                component_keys=components_to_update,
                instance_ids=[
                    str(item["instance_id"]) for item in eval_batch.trajectories
                ],
                includes_execution_after_evidence=True,
                checker_access=False,
            )
        return {"rules": eval_batch.trajectories}
