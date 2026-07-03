"""Data models for online GEPA planning optimization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.optimization.models import RepositoryRef


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

    def to_public_output(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "plan": self.plan,
            "patch": self.patch,
            "evaluator_result": self.evaluator_result,
            "attribution_hint": self.attribution_hint,
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
        }
