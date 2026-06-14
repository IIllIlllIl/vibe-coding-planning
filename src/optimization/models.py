"""Data models for the GEPA rule optimization pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RepositoryRef:
    repo: str
    base_commit: str
    instance_id: str


@dataclass(frozen=True)
class GEPACase:
    instance_id: str
    split: str
    resolved: bool
    issue_description: str
    plan: str
    repository: RepositoryRef
    asi: dict[str, Any]

    def checker_payload(self) -> dict[str, Any]:
        return {
            "issue_description": self.issue_description,
            "plan": self.plan,
            "repository": {
                "repo": self.repository.repo,
                "base_commit": self.repository.base_commit,
                "instance_id": self.repository.instance_id,
            },
        }


@dataclass(frozen=True)
class RepositoryEvidence:
    path: str
    symbol: str
    finding: str


@dataclass(frozen=True)
class CheckerOutput:
    predicted_resolved: bool
    decision_reason: str
    repository_evidence: tuple[RepositoryEvidence, ...]
    trajectory: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, include_trajectory: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "predicted_resolved": self.predicted_resolved,
            "decision_reason": self.decision_reason,
            "repository_evidence": [
                {
                    "path": item.path,
                    "symbol": item.symbol,
                    "finding": item.finding,
                }
                for item in self.repository_evidence
            ],
        }
        if include_trajectory:
            value["trajectory"] = list(self.trajectory)
        return value
