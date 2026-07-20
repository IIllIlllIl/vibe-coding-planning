"""Data models for online GEPA planning optimization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.optimization.models import RepositoryRef


ONLINE_OUTCOME_POLICY_VERSION = 4

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
    reflection_review: dict[str, Any] | None = None
    reflection_reviewer_trajectory: tuple[dict[str, Any], ...] = ()
    terminal_phase: str | None = None
    terminal_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.terminal_phase is None) != (self.terminal_reason is None):
            raise ValueError("terminal phase and reason must be provided together")

    def to_public_output(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "plan": self.plan,
            "patch": self.patch,
            "evaluator_result": self.evaluator_result,
            "terminal_phase": self.terminal_phase,
            "terminal_reason": self.terminal_reason,
        }

    def to_trace(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "generated_plan": self.plan,
            "plan_trajectory": list(self.plan_trajectory),
            "code_trajectory": list(self.code_trajectory),
            "generated_patch": self.patch,
            "evaluator_result": self.evaluator_result,
            "terminal_phase": self.terminal_phase,
            "terminal_reason": self.terminal_reason,
            "reflection_review": self.reflection_review,
            "reflection_reviewer_trajectory": list(
                self.reflection_reviewer_trajectory
            ),
        }

    def to_worker_payload(self) -> dict[str, Any]:
        return {
            **self.to_public_output(),
            "score": float(self.resolved),
            "plan_trajectory": list(self.plan_trajectory),
            "code_trajectory": list(self.code_trajectory),
            "reflection_review": self.reflection_review,
            "reflection_reviewer_trajectory": list(
                self.reflection_reviewer_trajectory
            ),
        }


def scored_agent_failure(
    *,
    phase: str,
    reason: str,
    evidence: dict[str, Any],
    evaluator_reason: str = "agent_failed_before_evaluation",
) -> OnlineRolloutOutput:
    """Create the one scored-zero representation for an exhausted Agent phase."""
    return OnlineRolloutOutput(
        resolved=False,
        plan=str(evidence.get("plan", "")),
        patch=str(evidence.get("patch", "")),
        plan_trajectory=tuple(evidence.get("plan_trajectory", [])),
        code_trajectory=tuple(evidence.get("code_trajectory", [])),
        evaluator_result={
            "status": "not_run",
            "reason": evaluator_reason,
        },
        terminal_phase=phase,
        terminal_reason=reason,
    )
