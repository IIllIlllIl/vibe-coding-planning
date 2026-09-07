"""Frozen paired inputs and assignments for SWE-Verified PCCE."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class PCCECase:
    source: Any
    baseline_plan: str
    baseline_resolved: bool | None
    baseline_outcome_sha256: str

    @property
    def instance_id(self) -> str:
        return self.source.instance_id


@dataclass(frozen=True)
class PCReviewAssignment:
    case: PCCECase
    review_index: int
    rejection_count: int
    input_plan: str
    previous_feedback: str


@dataclass(frozen=True)
class CEAssignment:
    case: PCCECase
    accepted_review_path: Path
    accepted_plan: str


@dataclass(frozen=True)
class PCCECheckerCase:
    source: Any
    plan: str
    asi: dict[str, Any]

    @property
    def instance_id(self) -> str:
        return self.source.instance_id

    @property
    def issue_description(self) -> str:
        return self.source.issue_description

    def checker_payload(self) -> dict[str, Any]:
        payload = self.source.agent_projection()
        payload["plan"] = self.plan
        return payload
