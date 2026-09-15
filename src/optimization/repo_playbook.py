"""Contracts for repository-aware concern-playbook review."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.optimization.playbook import RejectPlaybook


_EVIDENCE_SOURCES = frozenset({"issue", "plan", "repository"})
_REFLECTOR_TAGS = frozenset({"helpful", "neutral", "harmful"})
_CONFIDENCE = frozenset({"low", "medium", "high"})
_RECOVERY = frozenset({"c1", "c2", "c3", "unknown"})
_CALIBRATION = frozenset({"too_low", "appropriate", "too_high", "unknown"})


def render_concern_playbook(playbook: RejectPlaybook) -> str:
    """Render text and temporary ordinals, never internal IDs or counters."""
    lines = ["Review the Plan for these concerns:"]
    lines.extend(
        f"Concern {index}. {bullet.text}"
        for index, bullet in enumerate(playbook.bullets, start=1)
    )
    return "\n\n".join(lines)


@dataclass(frozen=True)
class RepoEvidence:
    source: str
    location: str | None
    observation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "location": self.location,
            "observation": self.observation,
        }


@dataclass(frozen=True)
class RepoConcernResult:
    rule_number: int
    triggered: bool
    level: int | None
    finding: str | None
    evidence: tuple[RepoEvidence, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_number": self.rule_number,
            "triggered": self.triggered,
            "level": self.level,
            "finding": self.finding,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RepoPlaybookCheckerOutput:
    rule_results: tuple[RepoConcernResult, ...]
    rejected: bool
    trajectory: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, include_trajectory: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule_results": [item.to_dict() for item in self.rule_results],
            "derived_decision": "REJECT" if self.rejected else "ACCEPT",
            "blocking_rule_numbers": [
                item.rule_number
                for item in self.rule_results
                if item.triggered and item.level == 2
            ],
            "advisory_rule_numbers": [
                item.rule_number
                for item in self.rule_results
                if item.triggered and item.level == 1
            ],
        }
        if include_trajectory:
            result["trajectory"] = list(self.trajectory)
        return result


def validate_repo_checker_result(
    value: Any,
    playbook: RejectPlaybook,
    *,
    trajectory: Sequence[Mapping[str, Any]] = (),
) -> RepoPlaybookCheckerOutput:
    """Validate case-level findings and derive the Level-2-only gate."""
    if not isinstance(value, dict) or set(value) != {"rule_results"}:
        raise ValueError("Repo Checker output must contain only rule_results")
    rows = value["rule_results"]
    if not isinstance(rows, list) or len(rows) != len(playbook.bullets):
        raise ValueError("Repo Checker must return one result per playbook bullet")

    parsed: list[RepoConcernResult] = []
    expected_keys = {
        "rule_number", "triggered", "level", "finding", "evidence", "reason"
    }
    for expected_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError(
                "Repo Checker rule result must contain exactly: "
                "rule_number, triggered, level, finding, evidence, reason"
            )
        if row["rule_number"] != expected_number:
            raise ValueError("Repo Checker rule results must use every ordinal in order")
        triggered = row["triggered"]
        if not isinstance(triggered, bool):
            raise ValueError("Repo Checker triggered must be boolean")
        level = row["level"]
        finding = row["finding"]
        evidence = row["evidence"]
        if not isinstance(evidence, list):
            raise ValueError("Repo Checker evidence must be a list")
        normalized_evidence: list[RepoEvidence] = []
        for item in evidence:
            if not isinstance(item, dict) or set(item) != {
                "source", "location", "observation"
            }:
                raise ValueError(
                    "Repo Checker evidence item must contain exactly: "
                    "source, location, observation"
                )
            if item["source"] not in _EVIDENCE_SOURCES:
                raise ValueError("Repo Checker evidence source is invalid")
            location = item["location"]
            if location is not None and (
                not isinstance(location, str) or not location.strip()
            ):
                raise ValueError("Repo Checker evidence location is invalid")
            observation = item["observation"]
            if not isinstance(observation, str) or not observation.strip():
                raise ValueError("Repo Checker evidence observation is invalid")
            normalized_evidence.append(
                RepoEvidence(
                    str(item["source"]),
                    location.strip() if isinstance(location, str) else None,
                    observation.strip(),
                )
            )
        reason = row["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Repo Checker rule reason must be non-empty")

        if triggered:
            if not isinstance(level, int) or isinstance(level, bool) or level not in {0, 1, 2}:
                raise ValueError("a triggered Repo Checker rule requires Level 0, 1, or 2")
            if not isinstance(finding, str) or not finding.strip():
                raise ValueError("a triggered Repo Checker rule requires a finding")
            if not normalized_evidence:
                raise ValueError("a triggered Repo Checker rule requires evidence")
            normalized_finding: str | None = finding.strip()
        else:
            if level is not None or finding is not None or normalized_evidence:
                raise ValueError(
                    "an untriggered Repo Checker rule requires null level/finding and no evidence"
                )
            normalized_finding = None

        parsed.append(
            RepoConcernResult(
                rule_number=expected_number,
                triggered=triggered,
                level=level,
                finding=normalized_finding,
                evidence=tuple(normalized_evidence),
                reason=reason.strip(),
            )
        )

    return RepoPlaybookCheckerOutput(
        rule_results=tuple(parsed),
        rejected=any(item.triggered and item.level == 2 for item in parsed),
        trajectory=tuple(dict(item) for item in trajectory),
    )


def validate_repo_reflector_review(
    value: Any,
    *,
    instance_id: str,
    playbook: RejectPlaybook,
) -> dict[str, Any]:
    """Validate retrospective recovery and level calibration per concern."""
    expected = {
        "instance_id",
        "case_analysis",
        "reusable_concerns",
        "uncertainty",
        "bullet_tags",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Repo Reflector review has an invalid schema")
    if value["instance_id"] != instance_id:
        raise ValueError("Repo Reflector instance ID mismatch")
    for key in {"case_analysis", "uncertainty"}:
        if value[key] is not None and (
            not isinstance(value[key], str) or not value[key].strip()
        ):
            raise ValueError(f"Repo Reflector {key} must be null or non-empty")

    concerns = value["reusable_concerns"]
    if not isinstance(concerns, list):
        raise ValueError("Repo Reflector reusable_concerns must be a list")
    normalized_concerns = []
    for concern in concerns:
        if not isinstance(concern, dict) or set(concern) != {
            "concern", "decision_time_support", "confidence"
        }:
            raise ValueError("Repo Reflector reusable concern has an invalid schema")
        if not isinstance(concern["concern"], str) or not concern["concern"].strip():
            raise ValueError("Repo Reflector reusable concern text must be non-empty")
        if (
            not isinstance(concern["decision_time_support"], str)
            or not concern["decision_time_support"].strip()
        ):
            raise ValueError("Repo Reflector decision-time support must be non-empty")
        if concern["confidence"] not in _CONFIDENCE:
            raise ValueError("Repo Reflector reusable concern confidence is invalid")
        normalized_concerns.append(dict(concern))

    tags = value["bullet_tags"]
    if not isinstance(tags, list) or len(tags) != len(playbook.bullets):
        raise ValueError("Repo Reflector must tag every active bullet")
    normalized_tags = []
    for bullet, tag in zip(playbook.bullets, tags, strict=True):
        if not isinstance(tag, dict) or set(tag) != {
            "id",
            "tag",
            "attribution",
            "confidence",
            "observed_recovery",
            "level_calibration",
        }:
            raise ValueError("Repo Reflector bullet tag has an invalid schema")
        if tag["id"] != bullet.id or tag["tag"] not in _REFLECTOR_TAGS:
            raise ValueError("Repo Reflector bullet tag identity or value is invalid")
        if tag["confidence"] not in _CONFIDENCE:
            raise ValueError("Repo Reflector confidence is invalid")
        if tag["observed_recovery"] not in _RECOVERY:
            raise ValueError("Repo Reflector observed recovery is invalid")
        if tag["level_calibration"] not in _CALIBRATION:
            raise ValueError("Repo Reflector level calibration is invalid")
        attribution = tag["attribution"]
        if attribution is not None and (
            not isinstance(attribution, str) or not attribution.strip()
        ):
            raise ValueError("Repo Reflector attribution must be null or non-empty")
        if tag["tag"] != "neutral" and attribution is None:
            raise ValueError("a helpful or harmful tag requires attribution")
        normalized = dict(tag)
        normalized["attribution"] = (
            attribution.strip() if isinstance(attribution, str) else None
        )
        normalized_tags.append(normalized)

    has_supported_analysis = bool(normalized_concerns) or any(
        tag["tag"] != "neutral"
        or tag["observed_recovery"] != "unknown"
        or tag["level_calibration"] != "unknown"
        for tag in normalized_tags
    )
    if has_supported_analysis and value["case_analysis"] is None:
        raise ValueError(
            "Repo Reflector case_analysis is required when evidence supports attribution"
        )

    result = dict(value)
    result["case_analysis"] = (
        value["case_analysis"].strip()
        if isinstance(value["case_analysis"], str)
        else None
    )
    result["uncertainty"] = (
        value["uncertainty"].strip()
        if isinstance(value["uncertainty"], str)
        else None
    )
    result["reusable_concerns"] = normalized_concerns
    result["bullet_tags"] = normalized_tags
    return result
