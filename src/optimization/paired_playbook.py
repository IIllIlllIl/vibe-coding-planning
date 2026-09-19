"""Contracts for binary repository-aware review of within-task Plan pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.optimization.playbook import RejectPlaybook
from src.optimization.repo_playbook import RepoEvidence

_EVIDENCE_SOURCES = frozenset({"issue", "plan", "repository"})
_TAGS = frozenset({"helpful", "neutral", "harmful"})
_CONFIDENCE = frozenset({"low", "medium", "high"})


@dataclass(frozen=True)
class PairedConcernResult:
    rule_number: int
    triggered: bool
    finding: str | None
    evidence: tuple[RepoEvidence, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_number": self.rule_number,
            "triggered": self.triggered,
            "finding": self.finding,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PairedCheckerOutput:
    rule_results: tuple[PairedConcernResult, ...]
    rejected: bool
    trajectory: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_results": [item.to_dict() for item in self.rule_results],
            "derived_decision": "REJECT" if self.rejected else "ACCEPT",
            "triggered_rule_numbers": [
                item.rule_number for item in self.rule_results if item.triggered
            ],
        }


def validate_paired_checker_result(
    value: Any,
    playbook: RejectPlaybook,
    *,
    trajectory: Sequence[Mapping[str, Any]] = (),
) -> PairedCheckerOutput:
    if not isinstance(value, dict) or set(value) != {"rule_results"}:
        raise ValueError("Paired Repo Checker output must contain only rule_results")
    rows = value["rule_results"]
    if not isinstance(rows, list) or len(rows) != len(playbook.bullets):
        raise ValueError("Paired Repo Checker must return one result per bullet")
    expected_keys = {"rule_number", "triggered", "finding", "evidence", "reason"}
    parsed: list[PairedConcernResult] = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError("Paired Repo Checker rule result has an invalid schema")
        if row["rule_number"] != number or not isinstance(row["triggered"], bool):
            raise ValueError("Paired Repo Checker rule identity/trigger is invalid")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("Paired Repo Checker reason must be non-empty")
        raw_evidence = row["evidence"]
        if not isinstance(raw_evidence, list):
            raise ValueError("Paired Repo Checker evidence must be a list")
        evidence: list[RepoEvidence] = []
        for item in raw_evidence:
            if not isinstance(item, dict) or set(item) != {
                "source",
                "location",
                "observation",
            }:
                raise ValueError("Paired Repo Checker evidence item is invalid")
            if item["source"] not in _EVIDENCE_SOURCES:
                raise ValueError("Paired Repo Checker evidence source is invalid")
            location = item["location"]
            if location is not None and (
                not isinstance(location, str) or not location.strip()
            ):
                raise ValueError("Paired Repo Checker evidence location is invalid")
            if (
                not isinstance(item["observation"], str)
                or not item["observation"].strip()
            ):
                raise ValueError("Paired Repo Checker evidence observation is invalid")
            evidence.append(
                RepoEvidence(
                    source=str(item["source"]),
                    location=location.strip() if isinstance(location, str) else None,
                    observation=item["observation"].strip(),
                )
            )
        finding = row["finding"]
        if row["triggered"]:
            if not isinstance(finding, str) or not finding.strip() or not evidence:
                raise ValueError(
                    "triggered paired concern requires finding and evidence"
                )
            normalized_finding: str | None = finding.strip()
        else:
            if finding is not None or evidence:
                raise ValueError(
                    "untriggered paired concern requires null/empty fields"
                )
            normalized_finding = None
        parsed.append(
            PairedConcernResult(
                rule_number=number,
                triggered=row["triggered"],
                finding=normalized_finding,
                evidence=tuple(evidence),
                reason=row["reason"].strip(),
            )
        )
    return PairedCheckerOutput(
        rule_results=tuple(parsed),
        rejected=any(item.triggered for item in parsed),
        trajectory=tuple(dict(item) for item in trajectory),
    )


def validate_paired_reflector_review(
    value: Any,
    *,
    instance_id: str,
    playbook: RejectPlaybook,
) -> dict[str, Any]:
    expected = {
        "instance_id",
        "pair_analysis",
        "reusable_concerns",
        "uncertainty",
        "bullet_tags",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Paired Reflector review has an invalid schema")
    if value["instance_id"] != instance_id:
        raise ValueError("Paired Reflector instance ID mismatch")
    for key in {"pair_analysis", "uncertainty"}:
        if value[key] is not None and (
            not isinstance(value[key], str) or not value[key].strip()
        ):
            raise ValueError(f"Paired Reflector {key} must be null or non-empty")
    concerns = value["reusable_concerns"]
    if not isinstance(concerns, list):
        raise ValueError("Paired Reflector reusable_concerns must be a list")
    normalized_concerns = []
    for concern in concerns:
        if not isinstance(concern, dict) or set(concern) != {
            "concern",
            "pair_support",
            "confidence",
        }:
            raise ValueError("Paired Reflector reusable concern is invalid")
        if (
            any(
                not isinstance(concern[key], str) or not concern[key].strip()
                for key in ("concern", "pair_support")
            )
            or concern["confidence"] not in _CONFIDENCE
        ):
            raise ValueError("Paired Reflector reusable concern content is invalid")
        normalized_concerns.append(dict(concern))
    tags = value["bullet_tags"]
    if not isinstance(tags, list) or len(tags) != len(playbook.bullets):
        raise ValueError("Paired Reflector must tag every active bullet")
    normalized_tags = []
    for bullet, tag in zip(playbook.bullets, tags, strict=True):
        if not isinstance(tag, dict) or set(tag) != {
            "id",
            "tag",
            "attribution",
            "confidence",
        }:
            raise ValueError("Paired Reflector bullet tag is invalid")
        attribution = tag["attribution"]
        if (
            tag["id"] != bullet.id
            or tag["tag"] not in _TAGS
            or tag["confidence"] not in _CONFIDENCE
            or (
                attribution is not None
                and (not isinstance(attribution, str) or not attribution.strip())
            )
            or (tag["tag"] != "neutral" and attribution is None)
        ):
            raise ValueError("Paired Reflector bullet tag content is invalid")
        normalized_tags.append(
            {
                **tag,
                "attribution": attribution.strip()
                if isinstance(attribution, str)
                else None,
            }
        )
    has_analysis = bool(normalized_concerns) or any(
        tag["tag"] != "neutral" for tag in normalized_tags
    )
    if has_analysis and value["pair_analysis"] is None:
        raise ValueError("Paired Reflector pair_analysis is required for attribution")
    return {
        **value,
        "pair_analysis": (
            value["pair_analysis"].strip()
            if isinstance(value["pair_analysis"], str)
            else None
        ),
        "uncertainty": (
            value["uncertainty"].strip()
            if isinstance(value["uncertainty"], str)
            else None
        ),
        "reusable_concerns": normalized_concerns,
        "bullet_tags": normalized_tags,
    }
