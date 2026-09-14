"""GEPA adapter and two-stage proposer for reject-playbook optimization."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from gepa.core.adapter import EvaluationBatch

from src.optimization.models import GEPACase
from src.optimization.playbook import (
    MAX_VISIBLE_TOKENS,
    RejectPlaybook,
    apply_curator_operations,
    classification_cost,
    manage_playbook_length,
    overlength_bullet_ids,
    validate_checker_result,
    validate_curator_proposal,
    validate_reflector_review,
)


class PlaybookChecker(Protocol):
    def __call__(
        self, checker_input: Mapping[str, str]
    ) -> tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]: ...


Reflector = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Curator = Callable[[RejectPlaybook, Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]], Mapping[str, Any]]


def _rule_identity(text: str) -> str:
    """Use exact normalized rule text as its cross-branch identity."""
    return " ".join(text.casefold().split())


class GlobalPlaybookCounters:
    """Accumulate unique-case attribution across branches, hidden from Checker."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._counts: dict[str, tuple[int, int]] = {}
        self._observations: dict[str, dict[str, str]] = {}
        if path is not None and path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != 1:
                raise ValueError("unsupported global counter ledger schema")
            self._counts = {
                key: (int(value["helpful"]), int(value["harmful"]))
                for key, value in raw["counts"].items()
            }
            self._observations = {
                key: dict(value) for key, value in raw["observations"].items()
            }

    def hydrate(self, playbook: RejectPlaybook) -> RejectPlaybook:
        bullets = []
        for bullet in playbook.bullets:
            key = _rule_identity(bullet.text)
            known = self._counts.get(key)
            if known is None:
                known = (bullet.helpful, bullet.harmful)
            else:
                known = (
                    max(known[0], bullet.helpful),
                    max(known[1], bullet.harmful),
                )
            self._counts[key] = known
            bullets.append(replace(bullet, helpful=known[0], harmful=known[1]))
        return RejectPlaybook(tuple(bullets))

    def apply_reviews(
        self,
        playbook: RejectPlaybook,
        reviews: Sequence[Mapping[str, Any]],
    ) -> tuple[RejectPlaybook, list[tuple[str, str, str]]]:
        pending: list[tuple[str, str, str]] = []
        deltas = {
            _rule_identity(bullet.text): {"helpful": 0, "harmful": 0}
            for bullet in playbook.bullets
        }
        identities = {
            bullet.id: _rule_identity(bullet.text) for bullet in playbook.bullets
        }
        for review in reviews:
            instance_id = str(review["instance_id"])
            for tag in review["bullet_tags"]:
                label = str(tag["tag"])
                if label not in {"helpful", "harmful"}:
                    continue
                key = identities[str(tag["id"])]
                if instance_id in self._observations.get(key, {}):
                    continue
                pending.append((key, instance_id, label))
                deltas[key][label] += 1
        counted = RejectPlaybook(tuple(
            replace(
                bullet,
                helpful=bullet.helpful
                + deltas[_rule_identity(bullet.text)]["helpful"],
                harmful=bullet.harmful
                + deltas[_rule_identity(bullet.text)]["harmful"],
            )
            for bullet in playbook.bullets
        ))
        return counted, pending

    def commit(
        self,
        counted: RejectPlaybook,
        pending: Sequence[tuple[str, str, str]],
    ) -> None:
        for key, instance_id, label in pending:
            self._observations.setdefault(key, {})[instance_id] = label
        for bullet in counted.bullets:
            self._counts[_rule_identity(bullet.text)] = (
                bullet.helpful,
                bullet.harmful,
            )
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "counts": self.snapshot(),
            "observations": self._observations,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            key: {"helpful": value[0], "harmful": value[1]}
            for key, value in sorted(self._counts.items())
        }


class ConfigurableRoundReflector:
    """Run the same per-case Reflector for a configured number of refinements."""

    def __init__(
        self,
        call: Callable[[Mapping[str, Any], Mapping[str, Any] | None], Mapping[str, Any]],
        *,
        rounds: int,
    ) -> None:
        if rounds < 1:
            raise ValueError("reflection rounds must be positive")
        self.call = call
        self.rounds = rounds
        self.last_rounds: list[Mapping[str, Any]] = []

    def __call__(self, record: Mapping[str, Any]) -> Mapping[str, Any]:
        prior = None
        history = []
        for _ in range(self.rounds):
            prior = self.call(record, prior)
            history.append(prior)
        self.last_rounds = history
        return prior or {}


class TwoStagePlaybookProposer:
    """Attribute each minibatch case, curate once, then enforce length."""

    def __init__(
        self,
        *,
        reflector: Reflector,
        curator: Curator,
        token_counter: Callable[[str], int],
        semantic_refiner: Callable[[RejectPlaybook], RejectPlaybook] | None = None,
        maximum_tokens: int = MAX_VISIBLE_TOKENS,
        harmful_weight: float = 5.0,
        batch_reflector: Callable[[Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]] | None = None,
        global_counter_path: Path | None = None,
    ) -> None:
        self.reflector = reflector
        self.curator = curator
        self.token_counter = token_counter
        self.semantic_refiner = semantic_refiner
        self.maximum_tokens = maximum_tokens
        self.harmful_weight = harmful_weight
        self.batch_reflector = batch_reflector
        self.successful_proposals = 0
        self.failures: list[dict[str, str]] = []
        self.last_length_report: dict[str, Any] | None = None
        self.global_counters = GlobalPlaybookCounters(global_counter_path)

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("playbook GEPA may update only rules")
        parent = self.global_counters.hydrate(
            RejectPlaybook.parse(candidate["rules"])
        )
        records = list(reflective_dataset["rules"])
        try:
            raw_reviews = (
                list(self.batch_reflector(records))
                if self.batch_reflector is not None
                else [self.reflector(record) for record in records]
            )
            reviews = [
                validate_reflector_review(
                    raw,
                    instance_id=str(record["instance_id"]),
                    playbook=parent,
                )
                for record, raw in zip(records, raw_reviews, strict=True)
            ]
            counted, pending_counter_events = self.global_counters.apply_reviews(
                parent, reviews
            )
            operations = self.curator(counted, reviews, records)
            proposed = apply_curator_operations(counted, operations)
            proposed = validate_curator_proposal(counted, proposed)
            proposed, report = manage_playbook_length(
                proposed,
                token_counter=self.token_counter,
                semantic_refiner=self.semantic_refiner,
                maximum_tokens=self.maximum_tokens,
                harmful_weight=self.harmful_weight,
            )
            self.last_length_report = report
        except Exception as exc:
            self.failures.append(
                {"error_type": type(exc).__name__, "error": str(exc)}
            )
            raise
        # Only a fully valid proposal contributes global evidence. Failed Agent
        # attempts and invalid Curator/Refiner output cannot increment counters.
        self.global_counters.commit(counted, pending_counter_events)
        proposed = self.global_counters.hydrate(proposed)
        self.successful_proposals += 1
        return {"rules": proposed.serialize()}


class PlaybookGEPAAdapter:
    """Evaluate structured candidates through a no-repository Checker."""

    def __init__(
        self,
        checker: PlaybookChecker | None,
        proposer: Any,
        *,
        batch_checker: Any = None,
        token_counter: Callable[[str], int] | None = None,
        maximum_bullet_tokens: int | None = None,
        score_table: Mapping[str, float] | None = None,
        invalid_score: float = -100.0,
    ) -> None:
        self.checker = checker
        self.batch_checker = batch_checker
        self.propose_new_texts = proposer
        self.token_counter = token_counter
        self.maximum_bullet_tokens = maximum_bullet_tokens
        self.score_table = dict(score_table) if score_table is not None else None
        self.invalid_score = float(invalid_score)

    def evaluate(
        self,
        batch: list[GEPACase],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        if set(candidate) != {"rules"}:
            raise ValueError("candidate must contain only rules")
        playbook = RejectPlaybook.parse(candidate["rules"])
        visible = playbook.render_for_checker()
        outputs: list[dict[str, Any]] = []
        scores: list[float] = []
        traces: list[dict[str, Any]] = []
        invalid_bullets: list[str] = []
        if self.maximum_bullet_tokens is not None:
            if self.token_counter is None:
                raise ValueError("bullet token cap requires a token counter")
            invalid_bullets = overlength_bullet_ids(
                playbook,
                token_counter=self.token_counter,
                maximum_bullet_tokens=self.maximum_bullet_tokens,
            )
        if invalid_bullets:
            for case in batch:
                output = {
                    "instance_id": case.instance_id,
                    "derived_decision": "INVALID",
                    "rule_results": [],
                    "invalid_reason": "bullet_token_limit_exceeded",
                    "invalid_bullet_ids": invalid_bullets,
                }
                outputs.append(output)
                scores.append(self.invalid_score)
                if capture_traces:
                    traces.append({
                        "instance_id": case.instance_id,
                        "ground_truth": "GOOD" if case.resolved else "BAD",
                        "resolved_proxy": case.resolved,
                        "score": self.invalid_score,
                        "issue": case.issue_description,
                        "plan": case.plan,
                        "internal_playbook": playbook.serialize(),
                        "checker_visible_playbook": visible,
                        "checker_output": output,
                        "historical_evidence": case.asi,
                    })
            return EvaluationBatch(
                outputs=outputs,
                scores=scores,
                trajectories=traces if capture_traces else None,
            )
        if self.batch_checker is not None:
            raw_results = self.batch_checker.evaluate_batch(batch, playbook)
        else:
            if self.checker is None:
                raise ValueError("no Playbook Checker configured")
            raw_results = [self.checker({
                "issue": case.issue_description,
                "plan": case.plan,
                "checker_visible_playbook": visible,
            }) for case in batch]
        for case, (raw, trajectory) in zip(batch, raw_results, strict=True):
            checked = validate_checker_result(
                raw, playbook, trajectory=trajectory
            )
            score = classification_cost(
                resolved=case.resolved,
                rejected=checked.rejected,
                score_table=self.score_table,
            )
            output = checked.to_dict()
            outputs.append({"instance_id": case.instance_id, **output})
            scores.append(score)
            if capture_traces:
                traces.append(
                    {
                        "instance_id": case.instance_id,
                        "ground_truth": "GOOD" if case.resolved else "BAD",
                        "resolved_proxy": case.resolved,
                        "score": score,
                        "issue": case.issue_description,
                        "plan": case.plan,
                        "internal_playbook": playbook.serialize(),
                        "checker_visible_playbook": visible,
                        "checker_output": output,
                        "historical_evidence": case.asi,
                    }
                )
        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=traces if capture_traces else None,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if components_to_update != ["rules"]:
            raise ValueError("playbook GEPA may update only rules")
        if eval_batch.trajectories is None:
            raise ValueError("playbook Reflection requires captured trajectories")
        return {"rules": eval_batch.trajectories}
