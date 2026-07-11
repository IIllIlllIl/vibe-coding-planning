"""GEPA adapter for online planning rollouts."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
from typing import Any, Mapping, Protocol, Sequence

from gepa.core.adapter import EvaluationBatch

from src.optimization.adapter import _exception_details
from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.online_models import OnlineGEPACase, OnlineRolloutOutput


class OnlineRolloutRunner(Protocol):
    def __call__(
        self,
        case: OnlineGEPACase,
        rules: str,
    ) -> OnlineRolloutOutput: ...


class OnlineRolloutBatchExecutor(Protocol):
    def evaluate(
        self,
        batch: list[OnlineGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[OnlineRolloutOutput]: ...


class OnlinePlanningGEPAAdapter:
    """Evaluate candidate planning rules by running current PCT rollouts."""

    def __init__(
        self,
        rollout: OnlineRolloutRunner,
        *,
        parallel: int = 1,
        proposer: Any = None,
        run_dir: Any = None,
        fail_on_rollout_error: bool = True,
        rollout_attempts: int = 1,
        batch_executor: OnlineRolloutBatchExecutor | None = None,
        resume_seed_evaluation: EvaluationBatch | None = None,
        resume_seed_key: tuple[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.rollout = rollout
        self.batch_executor = batch_executor
        self.parallel = parallel
        self.propose_new_texts = proposer
        self.run_dir = run_dir
        self.fail_on_rollout_error = fail_on_rollout_error
        self.rollout_attempts = rollout_attempts
        self.resume_seed_evaluation = resume_seed_evaluation
        self.resume_seed_key = resume_seed_key
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
        case: OnlineGEPACase,
        rules: str,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        last_exc: Exception | None = None
        for attempt in range(1, self.rollout_attempts + 1):
            try:
                output = self.rollout(case, rules)
                if self.audit is not None and attempt > 1:
                    self.audit.write(
                        "online_rollout_retried",
                        instance_id=case.instance_id,
                        candidate_sha256=text_sha256(rules),
                        successful_attempt=attempt,
                        max_attempts=self.rollout_attempts,
                    )
                score = float(output.resolved)
                public_output = {
                    "instance_id": case.instance_id,
                    **output.to_public_output(),
                }
                trace = None
                if capture_traces:
                    trace = {
                        "instance_id": case.instance_id,
                        "score": score,
                        **output.to_trace(),
                    }
                return public_output, score, trace
            except Exception as exc:
                last_exc = exc
                details = _exception_details(exc)
                if self.audit is not None:
                    self.audit.write(
                        "online_rollout_attempt_failed",
                        instance_id=case.instance_id,
                        candidate_sha256=text_sha256(rules),
                        attempt=attempt,
                        max_attempts=self.rollout_attempts,
                        **details,
                    )
        assert last_exc is not None
        details = _exception_details(last_exc)
        if self.audit is not None:
            self.audit.write(
                "online_rollout_failed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                attempts=self.rollout_attempts,
                **details,
            )
        if self.errors is not None:
            self.errors.write(
                "online_rollout_failed",
                instance_id=case.instance_id,
                candidate_sha256=text_sha256(rules),
                attempts=self.rollout_attempts,
                **details,
            )
        if self.fail_on_rollout_error:
            raise RuntimeError(
                "Online rollout operational failure for: "
                + str(case.instance_id)
            ) from last_exc
        output = {
            "instance_id": case.instance_id,
            "error": f"{type(last_exc).__name__}: {last_exc}",
        }
        trace = (
            {
                "instance_id": case.instance_id,
                "score": 0.0,
                "rollout_output": output,
            }
            if capture_traces
            else None
        )
        return output, 0.0, trace

    def _evaluate_parallel_fail_fast(
        self,
        batch: list[OnlineGEPACase],
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
                    try:
                        row = future.result()
                    except Exception:
                        for pending in active:
                            pending.cancel()
                        completed = sum(row is not None for row in rows)
                        if self.audit is not None:
                            self.audit.write(
                                "online_adapter_evaluation_aborted",
                                candidate_sha256=candidate_hash,
                                completed=completed,
                                in_flight=len(active),
                                not_started=len(batch)
                                - completed
                                - len(active),
                                reason="online_rollout_operational_failure",
                            )
                        raise
                    rows[index] = row
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
        batch: list[OnlineGEPACase],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        if set(candidate) != {"rules"} or not isinstance(candidate["rules"], str):
            raise ValueError("candidate must contain only the string component rules")
        candidate_hash = text_sha256(candidate["rules"])
        if self.resume_seed_evaluation is not None:
            expected_key = self.resume_seed_key
            actual_key = (candidate_hash, tuple(case.instance_id for case in batch))
            if capture_traces or expected_key is None or actual_key != expected_key:
                raise ValueError(
                    "GEPA first evaluation does not match the persisted seed validation"
                )
            resumed = self.resume_seed_evaluation
            self.resume_seed_evaluation = None
            self.resume_seed_key = None
            if len(resumed.scores) != len(batch):
                raise ValueError(
                    "checkpoint seed validation size does not match the current batch"
                )
            if self.audit is not None:
                self.audit.write(
                    "online_seed_validation_restored",
                    candidate_sha256=candidate_hash,
                    batch_size=len(batch),
                    source="gepa_state.bin",
                    submitted_rollouts=0,
                )
            return resumed
        if self.audit is not None:
            self.audit.write(
                "online_adapter_evaluation_started",
                candidate_sha256=candidate_hash,
                split=next((case.split for case in batch), None),
                batch_size=len(batch),
                capture_traces=capture_traces,
                parallel=self.parallel,
                candidate_components=["rules"],
                plan_agent_receives_candidate_rules=True,
                code_agent_receives_candidate_rules=False,
                historical_plan_available_to_plan_agent=False,
                historical_resolved_available_to_plan_agent=False,
                historical_asi_available_to_plan_agent=False,
            )
        if self.batch_executor is not None:
            try:
                rows = [
                    self._row_from_output(case, output, capture_traces)
                    for case, output in zip(
                        batch,
                        self.batch_executor.evaluate(
                            batch,
                            candidate["rules"],
                            capture_traces,
                        ),
                        strict=True,
                    )
                ]
            except Exception as exc:
                details = _exception_details(exc)
                if self.audit is not None:
                    self.audit.write(
                        "online_hpc_batch_failed",
                        candidate_sha256=candidate_hash,
                        batch_size=len(batch),
                        **details,
                    )
                if self.errors is not None:
                    self.errors.write(
                        "online_hpc_batch_failed",
                        candidate_sha256=candidate_hash,
                        batch_size=len(batch),
                        **details,
                    )
                raise
        elif self.fail_on_rollout_error and self.parallel > 1:
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
        outputs = [row[0] for row in rows]
        scores = [row[1] for row in rows]
        trajectories = [row[2] for row in rows if row[2] is not None]
        if self.audit is not None:
            self.audit.write(
                "online_adapter_evaluation_completed",
                candidate_sha256=candidate_hash,
                batch_size=len(batch),
                score_sum=sum(scores),
            )
        if self.run_dir is not None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with (self.run_dir / "evaluations.jsonl").open(
                "a",
                encoding="utf-8",
            ) as handle:
                for case, output, score in zip(
                    batch,
                    outputs,
                    scores,
                    strict=True,
                ):
                    handle.write(
                        json.dumps(
                            {
                                "candidate_sha256": candidate_hash,
                                "instance_id": case.instance_id,
                                "split": case.split,
                                "resolved": output.get("resolved"),
                                "score": score,
                                "output": output,
                                "mode": "online_planning",
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories,
        )

    @staticmethod
    def _row_from_output(
        case: OnlineGEPACase,
        output: OnlineRolloutOutput,
        capture_traces: bool,
    ) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
        score = float(output.resolved)
        public_output = {
            "instance_id": case.instance_id,
            **output.to_public_output(),
        }
        trace = None
        if capture_traces:
            trace = {
                "instance_id": case.instance_id,
                "score": score,
                **output.to_trace(),
            }
        return public_output, score, trace

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
                "online_reflective_dataset_created",
                candidate_sha256=text_sha256(candidate["rules"]),
                component_keys=components_to_update,
                instance_ids=[
                    str(item["instance_id"]) for item in eval_batch.trajectories
                ],
                includes_current_rollout_evidence=True,
                includes_historical_plan=False,
                includes_historical_resolved=False,
                includes_historical_asi=False,
            )
        return {"rules": eval_batch.trajectories}
