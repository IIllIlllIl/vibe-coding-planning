"""Behavioral Plan Acceptability data contracts.

These types deliberately do not reuse the historical ``resolved`` vocabulary.
The Checker projection is kept as a method on the case so every execution
backend has one explicit, testable information boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


TASK_SEMANTICS = "behavioral_plan_acceptability_v1"
ACCEPT = "ACCEPT"
DO_NOT_ACCEPT = "DO_NOT_ACCEPT"
DECISIONS = frozenset({ACCEPT, DO_NOT_ACCEPT})
REFLECTION_REPOSITORY_PROVENANCE_KEYS = frozenset(
    {
        "proxy_source",
        "recorded_branch_ref_available",
        "time_gap_seconds",
        "repository_state_semantics",
        "opaque_mutation_risk",
        "visible_evidence_conflicts",
    }
)


@dataclass(frozen=True)
class BehavioralRepositoryProxy:
    repo: str
    proxy_commit: str
    instance_id: str
    state_semantics: str = "approximate_pre_session_proxy"
    conflict_authority: str = "pre_p1_observed_tool_results"

    def checker_payload(self) -> dict[str, str]:
        return {
            "repo": self.repo,
            "proxy_commit": self.proxy_commit,
            "instance_id": self.instance_id,
            "state_semantics": self.state_semantics,
            "conflict_authority": self.conflict_authority,
        }


@dataclass(frozen=True)
class BehavioralGEPACase:
    instance_id: str
    split: str
    decision: str
    confidence: str
    signal: str
    pre_p1_context: tuple[dict[str, Any], ...]
    proposed_plan_p1: str
    repository: BehavioralRepositoryProxy
    reflection_evidence: dict[str, Any]
    audit_provenance: dict[str, Any]
    repetition_index: int | None = None

    @property
    def accepted(self) -> bool:
        return self.decision == ACCEPT

    def checker_payload(self) -> dict[str, Any]:
        """Return only information available at the P1 decision boundary."""
        return {
            "pre_p1_context": list(self.pre_p1_context),
            "proposed_plan_p1": self.proposed_plan_p1,
            "repository_proxy": self.repository.checker_payload(),
        }

    def worker_payload(self, repositories_root: str | Path) -> dict[str, Any]:
        """Add operational checkout data without adding supervision evidence."""
        return {
            "task_semantics": TASK_SEMANTICS,
            "checker_input": self.checker_payload(),
            "repository_materialization": {
                "mirror_path": str(
                    Path(repositories_root) / self.audit_provenance["mirror_relpath"]
                ),
                "proxy_commit": self.repository.proxy_commit,
            },
        }

    def reflection_repository_provenance(self) -> dict[str, Any]:
        return {
            key: self.audit_provenance[key]
            for key in REFLECTION_REPOSITORY_PROVENANCE_KEYS
            if key in self.audit_provenance
        }


@dataclass(frozen=True)
class BehavioralCheckerOutput:
    predicted_accept: bool
    decision_reason: str
    repository_evidence: tuple[dict[str, str], ...]
    trajectory: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, include_trajectory: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "predicted_accept": self.predicted_accept,
            "decision_reason": self.decision_reason,
            "repository_evidence": list(self.repository_evidence),
        }
        if include_trajectory:
            value["trajectory"] = list(self.trajectory)
        return value
