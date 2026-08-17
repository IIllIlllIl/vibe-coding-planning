"""Frozen paired inputs and phase assignments for PolyBench PCCE."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.polybench_pce.models import PolyBenchPCECase


@dataclass(frozen=True)
class PCCECase:
    source: PolyBenchPCECase
    baseline_plan: str
    baseline_resolved: bool
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
    """Structural Checker input with an explicit frozen PolyBench image."""

    source: PolyBenchPCECase
    plan: str
    asi: dict[str, Any]

    @property
    def instance_id(self) -> str:
        return self.source.instance_id

    @property
    def issue_description(self) -> str:
        return self.source.issue_description

    def checker_payload(self) -> dict[str, Any]:
        return {
            "issue_description": self.issue_description,
            "plan": self.plan,
            "repository": {
                "repo": self.source.repo,
                "base_commit": self.source.base_commit,
                "instance_id": self.source.instance_id,
                "dataset_type": "polybench",
                "image_name": self.source.image.requested_ref,
            },
        }
