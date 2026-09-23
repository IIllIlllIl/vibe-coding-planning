"""Contracts for binary repository-aware review of within-task Plan pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.optimization.playbook import RejectPlaybook
from src.optimization.repo_playbook import RepoEvidence

_EVIDENCE_SOURCES = frozenset({"issue", "plan", "repository"})
_TAGS = frozenset({"helpful", "neutral", "harmful"})


def paired_checker_uses_levels(config: Mapping[str, Any]) -> bool:
    contract = config.get("repo_checker", {}).get("output_contract", "binary_v1")
    if contract not in {"binary_v1", "levels_v1"}:
        raise ValueError("unknown paired Checker output contract")
    return contract == "levels_v1"


@dataclass(frozen=True)
class PairedConcernResult:
    rule_number: int
    triggered: bool
    finding: str | None
    evidence: tuple[RepoEvidence, ...]
    reason: str
    level: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_number": self.rule_number,
            **({"triggered": self.triggered} if self.level is None else {"level": self.level}),
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
        result = {
            "rule_results": [item.to_dict() for item in self.rule_results],
            "derived_decision": "REJECT" if self.rejected else "ACCEPT",
            "triggered_rule_numbers": [
                item.rule_number for item in self.rule_results if item.triggered
            ],
        }
        if any(item.level is not None for item in self.rule_results):
            result["blocking_rule_numbers"] = [i.rule_number for i in self.rule_results if i.level == 2]
            result["warning_rule_numbers"] = [i.rule_number for i in self.rule_results if i.level == 1]
        return result


def validate_paired_checker_result(
    value: Any,
    playbook: RejectPlaybook,
    *,
    trajectory: Sequence[Mapping[str, Any]] = (),
    levels: bool = False,
) -> PairedCheckerOutput:
    if not isinstance(value, dict) or set(value) != {"rule_results"}:
        raise ValueError("Paired Repo Checker output must contain only rule_results")
    rows = value["rule_results"]
    if not isinstance(rows, list) or len(rows) != len(playbook.bullets):
        raise ValueError("Paired Repo Checker must return one result per bullet")
    expected_keys = {"rule_number", "level" if levels else "triggered", "finding", "evidence", "reason"}
    parsed: list[PairedConcernResult] = []
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError("Paired Repo Checker rule result has an invalid schema")
        if type(row["rule_number"]) is not int or row["rule_number"] != number:
            raise ValueError("Paired Repo Checker rule identity/trigger is invalid")
        level = row.get("level") if levels else None
        if levels:
            if type(level) is not int or level not in {0, 1, 2}:
                raise ValueError("Paired Repo Checker level must be 0, 1, or 2")
            triggered = level > 0
        else:
            if not isinstance(row["triggered"], bool):
                raise ValueError("Paired Repo Checker trigger must be boolean")
            triggered = row["triggered"]
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
        if triggered:
            if not isinstance(finding, str) or not finding.strip() or not evidence:
                raise ValueError(
                    "triggered paired concern requires finding and evidence"
                )
            normalized_finding: str | None = finding.strip()
        else:
            if levels and finding is not None:
                # Level 0 may still record a minor, readily repairable Plan
                # issue. It is neither a warning nor a blocking trigger.
                if not isinstance(finding, str) or not finding.strip() or not evidence:
                    raise ValueError("Level 0 finding requires nonempty evidence")
                normalized_finding = finding.strip()
            else:
                if finding is not None or evidence:
                    raise ValueError(
                        "untriggered paired concern requires null/empty fields"
                    )
                normalized_finding = None
        parsed.append(
            PairedConcernResult(
                rule_number=number,
                triggered=triggered,
                finding=normalized_finding,
                evidence=tuple(evidence),
                reason=row["reason"].strip(),
                level=level,
            )
        )
    return PairedCheckerOutput(
        rule_results=tuple(parsed),
        rejected=any(item.level == 2 if levels else item.triggered for item in parsed),
        trajectory=tuple(dict(item) for item in trajectory),
    )


def validate_paired_reflector_review(
    value: Any,
    *,
    instance_id: str,
    playbook: RejectPlaybook,
    structured_recovery: bool = False,
    structured_abstraction: bool = False,
    distilled_curation: bool = False,
    fact_links: bool = False,
) -> dict[str, Any]:
    if structured_abstraction and not structured_recovery:
        raise ValueError("Paired Reflector abstraction requires structured recovery")
    if distilled_curation and not (structured_recovery and structured_abstraction):
        raise ValueError(
            "Distilled paired Reflection requires recovery and abstraction"
        )
    if fact_links and not distilled_curation:
        raise ValueError("Paired Reflector fact links require distilled curation")
    expected = {
        "instance_id",
        "pair_analysis",
        "reusable_concerns",
        "uncertainty",
        "bullet_tags",
    }
    if structured_recovery:
        expected.add("side_findings")
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
        if distilled_curation:
            allowed = {
                "developer_concern",
                "decision_time_basis",
                "pair_evidence",
            }
            if fact_links:
                allowed.add("supporting_side_findings")
        else:
            allowed = (
                {"case_mechanism", "developer_concern", "pair_support"}
                if structured_abstraction
                else {"concern", "pair_support"}
            )
        if structured_recovery and not distilled_curation:
            allowed.add("curation_assessment")
        accepted_concern_keys = (
            (allowed,)
            if structured_abstraction
            else (allowed, allowed | {"confidence"})
        )
        if not isinstance(concern, dict) or set(concern) not in accepted_concern_keys:
            raise ValueError("Paired Reflector reusable concern is invalid")
        text_keys = (
            ("developer_concern", "decision_time_basis", "pair_evidence")
            if distilled_curation
            else (
                ("case_mechanism", "developer_concern", "pair_support")
                if structured_abstraction
                else ("concern", "pair_support")
            )
        )
        if any(
            not isinstance(concern[key], str) or not concern[key].strip()
            for key in text_keys
        ):
            raise ValueError("Paired Reflector reusable concern content is invalid")
        # Older frozen prompts emitted confidence; it is not a Level and has
        # no defined role in curation. Accept legacy output, but do not pass
        # the field to the Curator or persist it in normalized reviews.
        normalized = {key: concern[key].strip() for key in text_keys}
        if fact_links:
            links = concern["supporting_side_findings"]
            if not isinstance(links, list) or not links:
                raise ValueError(
                    "Paired Reflector reusable concern requires fact links"
                )
            normalized_links = []
            seen_links: set[tuple[str, int]] = set()
            for link in links:
                if not isinstance(link, dict) or set(link) != {
                    "side",
                    "finding_number",
                }:
                    raise ValueError("Paired Reflector fact link is invalid")
                side = link["side"]
                number = link["finding_number"]
                identity = (side, number)
                if (
                    side not in {"resolved", "unresolved"}
                    or type(number) is not int
                    or number < 1
                    or identity in seen_links
                ):
                    raise ValueError("Paired Reflector fact link content is invalid")
                seen_links.add(identity)
                normalized_links.append(
                    {"side": side, "finding_number": number}
                )
            normalized["supporting_side_findings"] = normalized_links
        if structured_recovery and not distilled_curation:
            assessment = concern["curation_assessment"]
            expected_assessment = {
                "pair_relation",
                "decision_time_status",
                "coder_repairability",
            }
            if structured_abstraction:
                expected_assessment.add("evidence_role")
            else:
                expected_assessment.update({"recommendation", "reason"})
            if not isinstance(assessment, dict) or set(assessment) != expected_assessment:
                raise ValueError("Paired Reflector curation assessment is invalid")
            if assessment["pair_relation"] not in {
                "distinguishes",
                "shared",
                "confounded",
            }:
                raise ValueError("Paired Reflector pair relation is invalid")
            if structured_abstraction and assessment["evidence_role"] not in {
                "outcome_explanatory",
                "repairability_calibration",
                "incidental",
                "confounded",
            }:
                raise ValueError("Paired Reflector evidence role is invalid")
            allowed_decision_time_statuses = (
                {"supported", "underdetermined"}
                if structured_abstraction
                else {"supported", "underdetermined", "hindsight_only"}
            )
            if assessment["decision_time_status"] not in allowed_decision_time_statuses:
                raise ValueError("Paired Reflector decision-time status is invalid")
            if assessment["coder_repairability"] not in {
                "readily_compensated",
                "required_substantial_adjustment",
                "not_repaired",
                "mixed_or_unclear",
            }:
                raise ValueError("Paired Reflector Coder repairability is invalid")
            if not structured_abstraction:
                if assessment["recommendation"] not in {"promote", "defer"}:
                    raise ValueError(
                        "Paired Reflector promotion recommendation is invalid"
                    )
                if (
                    assessment["decision_time_status"] == "hindsight_only"
                    and assessment["recommendation"] != "defer"
                ):
                    raise ValueError(
                        "Paired Reflector hindsight-only concern must be deferred"
                    )
                if (
                    not isinstance(assessment["reason"], str)
                    or not assessment["reason"].strip()
                ):
                    raise ValueError("Paired Reflector curation reason is required")
                assessment = {
                    **assessment,
                    "reason": assessment["reason"].strip(),
                }
            normalized["curation_assessment"] = dict(assessment)
        normalized_concerns.append(normalized)
    normalized_sides = []
    if structured_recovery:
        sides = value["side_findings"]
        if not isinstance(sides, list) or len(sides) != 2:
            raise ValueError("Paired Reflector requires exactly two side findings")
        for expected_side, side in zip(("resolved", "unresolved"), sides, strict=True):
            if not isinstance(side, dict) or set(side) != {"side", "plan_concerns"}:
                raise ValueError("Paired Reflector side finding is invalid")
            if side["side"] != expected_side or not isinstance(side["plan_concerns"], list):
                raise ValueError("Paired Reflector side order or concerns are invalid")
            normalized_items = []
            for item in side["plan_concerns"]:
                keys = {"concern", "decision_time_support", "coder_response", "outcome_relation"}
                if not isinstance(item, dict) or set(item) != keys:
                    raise ValueError("Paired Reflector Plan concern is invalid")
                if item["coder_response"] not in {"followed", "compensated", "departed", "unknown"}:
                    raise ValueError("Paired Reflector Coder response is invalid")
                if any(not isinstance(item[key], str) or not item[key].strip() for key in keys - {"coder_response"}):
                    raise ValueError("Paired Reflector Plan concern lacks an explanation")
                normalized_items.append({key: value.strip() for key, value in item.items()})
            normalized_sides.append({"side": expected_side, "plan_concerns": normalized_items})
        if fact_links:
            side_counts = {
                side["side"]: len(side["plan_concerns"])
                for side in normalized_sides
            }
            for concern in normalized_concerns:
                for link in concern["supporting_side_findings"]:
                    if link["finding_number"] > side_counts[link["side"]]:
                        raise ValueError(
                            "Paired Reflector fact link targets a missing side finding"
                        )
    tags = value["bullet_tags"]
    if not isinstance(tags, list) or len(tags) != len(playbook.bullets):
        raise ValueError("Paired Reflector must tag every active bullet")
    normalized_tags = []
    for bullet, tag in zip(playbook.bullets, tags, strict=True):
        accepted_tag_keys = (
            ({"id", "tag", "attribution"},)
            if structured_abstraction
            else (
                {"id", "tag", "attribution"},
                {"id", "tag", "attribution", "confidence"},
            )
        )
        if not isinstance(tag, dict) or set(tag) not in accepted_tag_keys:
            raise ValueError("Paired Reflector bullet tag is invalid")
        attribution = tag["attribution"]
        if (
            tag["id"] != bullet.id
            or tag["tag"] not in _TAGS
            or (
                attribution is not None
                and (not isinstance(attribution, str) or not attribution.strip())
            )
            or (tag["tag"] != "neutral" and attribution is None)
        ):
            raise ValueError("Paired Reflector bullet tag content is invalid")
        normalized_tags.append(
            {
                "id": tag["id"],
                "tag": tag["tag"],
                "attribution": attribution.strip()
                if isinstance(attribution, str)
                else None,
            }
        )
    has_analysis = bool(normalized_concerns) or any(
        tag["tag"] != "neutral" for tag in normalized_tags
    ) or any(side["plan_concerns"] for side in normalized_sides)
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
        **({"side_findings": normalized_sides} if structured_recovery else {}),
    }
