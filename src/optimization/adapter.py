"""GEPA adapter for fixed Checker evaluation."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from gepa.core.adapter import EvaluationBatch

from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.checker import CheckerRunner
from src.optimization.models import GEPACase


def _exception_details(exc: Exception) -> dict[str, Any]:
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


class CheckerGEPAAdapter:
    def __init__(
        self,
        checker: CheckerRunner,
        *,
        parallel: int = 1,
        proposer: Any = None,
        run_dir: Path | None = None,
        fail_on_checker_error: bool = False,
        checker_attempts: int = 1,
        startup_seed_replay: dict[
            str, tuple[dict[str, Any], float]
        ] | None = None,
        seed_rules_sha256: str | None = None,
    ) -> None:
        self.checker = checker
        self.parallel = parallel
        self.propose_new_texts = proposer
        self.run_dir = run_dir
        self.fail_on_checker_error = fail_on_checker_error
        self.checker_attempts = checker_attempts
        self.startup_seed_replay = startup_seed_replay
        self.seed_rules_sha256 = seed_rules_sha256
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

    def _evaluate_one(
        self,
        case: GEPACase,
        rules: str,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        last_exc: Exception | None = None
        for attempt in range(1, self.checker_attempts + 1):
            try:
                return self._evaluate_one_attempt(
                    case,
                    rules,
                    capture_traces,
                    attempt=attempt,
                )
            except Exception as exc:
                last_exc = exc
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
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        output = self.checker(case, rules)
        if self.audit is not None and attempt > 1:
            self.audit.write(
                "checker_evaluation_retried",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                successful_attempt=attempt,
                max_attempts=self.checker_attempts,
            )
        score = float(output.predicted_resolved == case.resolved)
        public_output = {
            "instance_id": case.instance_id,
            **output.to_dict(),
        }
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "expected_resolved": case.resolved,
                "score": score,
                "checker_output": output.to_dict(include_trajectory=True),
                **case.asi,
            }
        return public_output, score, trace

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
                parallel=self.parallel,
                checker_attempts=self.checker_attempts,
            )
        if self.fail_on_checker_error and self.parallel > 1:
            rows = self._evaluate_parallel_fail_fast(
                batch,
                candidate["rules"],
                capture_traces,
                candidate_hash,
            )
        else:
            with ThreadPoolExecutor(max_workers=self.parallel) as executor:
                rows = list(
                    executor.map(
                        lambda case: self._evaluate_one(
                            case,
                            candidate["rules"],
                            capture_traces,
                        ),
                        batch,
                    )
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
                    handle.write(
                        json.dumps(
                            {
                                "candidate_sha256": candidate_hash,
                                "instance_id": case.instance_id,
                                "split": case.split,
                                "resolved": case.resolved,
                                "score": score,
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
        errors = [output for output in result.outputs if "error" in output]
        if self.fail_on_checker_error and errors:
            instance_ids = [str(output["instance_id"]) for output in errors]
            raise RuntimeError(
                "Checker operational failure for: "
                + ", ".join(instance_ids)
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
