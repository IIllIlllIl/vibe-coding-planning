"""Data models for online GEPA planning optimization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.optimization.models import RepositoryRef


ONLINE_OUTCOME_POLICY_VERSION = 3

@dataclass(frozen=True)
class OnlineGEPACase:
    """A deploy-time instance for online planning optimization.

    Unlike :class:`src.optimization.models.GEPACase`, this object deliberately
    has no historical plan, resolved label, patch, evaluator result, or ASI.
    Those fields must be produced by the current candidate rollout.
    """

    instance_id: str
    split: str
    issue_description: str
    repository: RepositoryRef

    def rollout_payload(self) -> dict[str, Any]:
        return {
            "issue_description": self.issue_description,
            "repository": {
                "repo": self.repository.repo,
                "base_commit": self.repository.base_commit,
                "instance_id": self.repository.instance_id,
            },
        }


@dataclass(frozen=True)
class OnlineRolloutOutput:
    """Result of one candidate rules rollout on one instance."""

    resolved: bool
    plan: str
    patch: str
    plan_trajectory: tuple[dict[str, Any], ...]
    code_trajectory: tuple[dict[str, Any], ...]
    evaluator_result: dict[str, Any]
    attribution_hint: dict[str, Any] = field(default_factory=dict)
    reflection_review: dict[str, Any] | None = None
    outcome_status: str = "scored"
    score_valid: bool = True
    evaluator_status: str = "completed"
    evaluator_resolved: bool | None = None
    terminal_phase: str | None = None
    terminal_reason: str | None = None
    failure_origin: str | None = None

    def __post_init__(self) -> None:
        if self.outcome_status not in {"scored", "invalid"}:
            raise ValueError(f"unsupported outcome status: {self.outcome_status}")
        if self.score_valid != (self.outcome_status == "scored"):
            raise ValueError("score_valid must agree with outcome_status")
        if self.evaluator_status == "not_run" and self.evaluator_resolved is not None:
            raise ValueError("an evaluator that did not run cannot have a result")

    def to_public_output(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "plan": self.plan,
            "patch": self.patch,
            "evaluator_result": self.evaluator_result,
            "attribution_hint": self.attribution_hint,
            "outcome_status": self.outcome_status,
            "score_valid": self.score_valid,
            "evaluator_status": self.evaluator_status,
            "evaluator_resolved": self.evaluator_resolved,
            "terminal_phase": self.terminal_phase,
            "terminal_reason": self.terminal_reason,
            "failure_origin": self.failure_origin,
        }

    def to_trace(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "generated_plan": self.plan,
            "plan_trajectory": list(self.plan_trajectory),
            "code_trajectory": list(self.code_trajectory),
            "generated_patch": self.patch,
            "evaluator_result": self.evaluator_result,
            "attribution_hint": self.attribution_hint,
            "outcome_status": self.outcome_status,
            "score_valid": self.score_valid,
            "evaluator_status": self.evaluator_status,
            "evaluator_resolved": self.evaluator_resolved,
            "terminal_phase": self.terminal_phase,
            "terminal_reason": self.terminal_reason,
            "failure_origin": self.failure_origin,
            "reflection_review": self.reflection_review,
        }
