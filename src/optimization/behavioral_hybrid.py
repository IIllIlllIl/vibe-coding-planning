"""Opt-in hybrid candidates on the existing SWE-chat GEPA adapter.

Agent runners are injected, so the production transport remains an explicit
engineering choice rather than silently falling back to an unisolated runtime.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from src.optimization.behavioral_adapter import BehavioralGEPAAdapter
from src.optimization.behavioral_models import BehavioralCheckerOutput


@dataclass(frozen=True)
class HybridLimits:
    max_items: int
    shared_guide_words: int
    condition_words: int
    item_guide_words: int

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("hybrid limits must be positive integers")


def _text(value: Any, limit: int, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    if len(value.split()) > limit:
        raise ValueError(f"{field} exceeds {limit} whitespace-delimited words")


def validate_candidate(raw: str, limits: HybridLimits) -> dict[str, Any]:
    """Validate, never truncate or rewrite, the Agent's complete candidate."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != {"schema_version", "shared_guide", "checklist"}:
        raise ValueError("candidate fields: schema_version, shared_guide, checklist")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    _text(value["shared_guide"], limits.shared_guide_words, "shared_guide")
    items = value["checklist"]
    if not isinstance(items, list) or not 1 <= len(items) <= limits.max_items:
        raise ValueError(f"checklist must contain 1..{limits.max_items} items")
    ids, conditions = set(), set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"id", "condition", "guide"}:
            raise ValueError("checklist item fields: id, condition, guide")
        _text(item["id"], 1, "id")
        from src.optimization.playbook import PlaybookBullet
        PlaybookBullet.from_dict(PlaybookBullet(item["id"], item["condition"]).to_dict())
        _text(item["condition"], limits.condition_words, "condition")
        _text(item["guide"], limits.item_guide_words, "guide")
        condition_identity = " ".join(item["condition"].casefold().split())
        if item["id"] in ids or condition_identity in conditions:
            raise ValueError("duplicate checklist id or condition")
        ids.add(item["id"])
        conditions.add(condition_identity)
    return value


def checker_projection(artifact):
    """Mechanical Checker view: output ordinals, not branch-local learning IDs.

    Conditions and guides are unchanged; the original artifact remains the
    authority for attribution and validation.
    """
    return {"schema_version": artifact["schema_version"],
            "shared_guide": artifact["shared_guide"],
            "checklist": [{"rule_number": number, "condition": item["condition"],
                           "guide": item["guide"]}
                          for number, item in enumerate(artifact["checklist"], 1)]}


def checker_bridge(agent: Callable, limits: HybridLimits):
    """Only decision-time inputs cross into Checker; preserve its raw trace."""
    def check(case, raw):
        artifact = validate_candidate(raw, limits)
        submission, trajectory = agent(case.checker_payload(), checker_projection(artifact))
        return parse_hybrid_checker_output(submission, artifact, trajectory=trajectory)
    return check


def parse_hybrid_checker_output(submission, artifact, *, trajectory=()):
    """Reuse the current binary ACE contract; derive the overall decision only."""
    from src.optimization.behavioral_hybrid_ace import _playbook
    from src.optimization.paired_playbook import validate_paired_checker_result
    parsed = validate_paired_checker_result(submission, _playbook(artifact), require_reason=False)
    triggered = [str(row.rule_number) for row in parsed.rule_results if row.triggered]
    return BehavioralCheckerOutput(
        predicted_accept=not parsed.rejected,
        decision_reason="Checklist triggers: " + (", ".join(triggered) if triggered else "none"),
        repository_evidence=(), trajectory=tuple(trajectory),
        rule_results=tuple(submission["rule_results"]))


class HybridBehavioralAdapter(BehavioralGEPAAdapter):
    """Retain SWE-chat scoring, repetitions and caching; enforce learning boundary."""
    def __init__(self, checker, *, limits: HybridLimits, **kwargs):
        super().__init__(checker, **kwargs)
        self.primary_metric = "human_decision_utility"
        self.limits = limits
        self._physical_traces = []

    def _score(self, case, predicted_accept):
        """Single-Plan utility against the recorded human decision, not accuracy."""
        if case.accepted:
            return 1.0 if predicted_accept else -1.0
        return 0.0 if predicted_accept else 1.0

    def _evaluate_physical(self, case, guideline, capture_traces):
        row = super()._evaluate_physical(case, guideline, capture_traces)
        if capture_traces:
            row[2]["checker_input"] = case.checker_payload()
            self._physical_traces.append(row[2])
        return row

    def _row_from_checker_output(self, case, output, capture_traces):
        row = super()._row_from_checker_output(case, output, capture_traces)
        if capture_traces:
            row[2]["checker_input"] = case.checker_payload()
            self._physical_traces.append(row[2])
        return row

    def evaluate(self, batch, candidate, capture_traces=False):
        try:
            validate_candidate(candidate["rules"], self.limits)
        except (ValueError, TypeError):
            from gepa.core.adapter import EvaluationBatch
            return EvaluationBatch(outputs=[{"invalid_candidate": True} for _ in batch],
                                   scores=[-100.0 for _ in batch],
                                   trajectories=None)
        if capture_traces and any(case.split != "train" for case in batch):
            raise ValueError("validation/test trajectories cannot enter learning evidence")
        self._physical_traces = []
        result = super().evaluate(batch, candidate, capture_traces)
        if capture_traces:
            repetitions = self.train_case_repetitions
            for index, record in enumerate(result.trajectories or []):
                record["split"] = "train"
                record["checker_input"] = batch[index].checker_payload()
                if repetitions > 1:
                    record["physical_investigations"] = self._physical_traces[
                        index * repetitions : (index + 1) * repetitions
                    ]
        return result
