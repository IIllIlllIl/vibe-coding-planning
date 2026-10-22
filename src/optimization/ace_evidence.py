"""Shared ACE Curator evidence layout, extracted from the completed formal path.

Callers adapt artifact semantics; this module only indexes and writes evidence.
"""
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.optimization.hpc.task_batch import atomic_json
from src.optimization.paired_playbook import ACE_PAIRED_REFLECTION_CONTRACTS


def write_curator_evidence(
    evidence_dir: Path,
    counted_playbook: Mapping[str, Any],
    bullets: Sequence[Any],
    reviews: Sequence[Mapping[str, Any]],
    *,
    feedback_history: Mapping[str, Any] | None = None,
    evidence_contract: str = "ace_v2",
    require_concern_coverage: bool = False,
    require_curator_self_check: bool = False,
) -> list[str]:
    """Preserve formal ACE required/optional files and complete review fields."""
    atomic_json(
        evidence_dir / "counted_playbook.json", counted_playbook
    )
    atomic_json(evidence_dir / "case_reflections.json", list(reviews))
    history_files = ["rule_feedback_history_index.json", "rule_feedback_history.json"] if feedback_history is not None else []
    if feedback_history is not None:
        atomic_json(evidence_dir / "rule_feedback_history.json", feedback_history)
        index = []
        for bullet in bullets:
            rule_identity = " ".join(bullet.text.casefold().split())
            tags = [event["tag"] for event in feedback_history["events"].values()
                    if event["rule_identity"] == rule_identity]
            index.append({
                "bullet_id": bullet.id,
                "rule_identity": rule_identity,
                "counts": {"helpful": bullet.helpful, "harmful": bullet.harmful, "neutral": bullet.neutral},
                "history_observations": {label: tags.count(label) for label in ("helpful", "harmful", "neutral")},
            })
        atomic_json(evidence_dir / "rule_feedback_history_index.json", {
            "schema_version": 1,
            "source": feedback_history["source"],
            "interpretation": feedback_history["interpretation"],
            "counting_policy": feedback_history["counting_policy"],
            "details_file": "rule_feedback_history.json",
            "lookup": "Select events by rule_identity. Each event links its full Reflector report and supplied artifact context by SHA-256. Consult the explanations relevant to the rules being maintained; earlier assessments may disagree.",
            "bullets": index,
        })
    reflection_index = []
    concern_ids: list[str] = []
    for review in reviews:
        if evidence_contract in ACE_PAIRED_REFLECTION_CONTRACTS:
            numbered = []
            for index, insight in enumerate(
                review.get("key_insights", []), start=1
            ):
                prefix = "i" if evidence_contract == "ace_v2" else "c"
                insight_id = f'{review["instance_id"]}:{prefix}{index}'
                concern_ids.append(insight_id)
                numbered.append({"id": insight_id, **dict(insight)})
            reflection_index.append(
                {
                    "instance_id": review.get("instance_id"),
                    "reasoning": review.get("reasoning"),
                    "error_identification": review.get("error_identification"),
                    "root_cause_analysis": review.get("root_cause_analysis"),
                    "correct_approach": review.get("correct_approach"),
                    "key_insights": numbered,
                    "bullet_tags": review.get("bullet_tags", []),
                }
            )
            continue
        if evidence_contract == "distilled_v1":
            numbered = []
            linked_findings: dict[str, dict[str, Any]] = {}
            for index, concern in enumerate(
                review.get("reusable_concerns", []), start=1
            ):
                concern_id = f'{review["instance_id"]}:c{index}'
                concern_ids.append(concern_id)
                normalized_concern = dict(concern)
                links = normalized_concern.pop("supporting_side_findings", [])
                supporting_ids = []
                for link in links:
                    prefix = "r" if link["side"] == "resolved" else "u"
                    finding_id = (
                        f'{review["instance_id"]}:{prefix}'
                        f'{link["finding_number"]}'
                    )
                    supporting_ids.append(finding_id)
                    side = next(
                        item
                        for item in review["side_findings"]
                        if item["side"] == link["side"]
                    )
                    finding = side["plan_concerns"][link["finding_number"] - 1]
                    linked_findings[finding_id] = {
                        "id": finding_id,
                        "side": link["side"],
                        **finding,
                    }
                numbered.append(
                    {
                        "id": concern_id,
                        **normalized_concern,
                        **(
                            {"supporting_side_finding_ids": supporting_ids}
                            if supporting_ids
                            else {}
                        ),
                    }
                )
            reflection_index.append(
                {
                    "instance_id": review.get("instance_id"),
                    "reusable_concerns": numbered,
                    **(
                        {"linked_side_findings": list(linked_findings.values())}
                        if linked_findings
                        else {}
                    ),
                    "uncertainty": review.get("uncertainty"),
                }
            )
            continue
        summary = {
            "instance_id": review.get("instance_id"),
            "uncertainty": review.get("uncertainty"),
            "bullet_tags": review.get("bullet_tags", []),
        }
        if "reusable_concerns" in review:
            if require_concern_coverage:
                summary["reusable_concerns"] = []
                for index, concern in enumerate(review["reusable_concerns"], start=1):
                    concern_id = f'{review["instance_id"]}:c{index}'
                    concern_ids.append(concern_id)
                    summary["reusable_concerns"].append({"id": concern_id, **concern})
                summary["pair_analysis"] = review.get("pair_analysis")
                summary["side_findings"] = []
                for side in review.get("side_findings", []):
                    prefix = "r" if side["side"] == "resolved" else "u"
                    numbered = []
                    for index, finding in enumerate(side["plan_concerns"], start=1):
                        finding_id = f'{review["instance_id"]}:{prefix}{index}'
                        concern_ids.append(finding_id)
                        numbered.append({"id": finding_id, **finding})
                    summary["side_findings"].append({"side": side["side"], "plan_concerns": numbered})
            else:
                summary["reusable_concerns"] = review["reusable_concerns"]
        else:
            summary["key_insight"] = review.get("key_insight")
        reflection_index.append(summary)
    atomic_json(evidence_dir / "reflection_index.json", reflection_index)
    atomic_json(
        evidence_dir / "manifest.json",
        {
            "schema_version": 1,
            "case_count": len(reviews),
            "files": [
                "counted_playbook.json",
                "reflection_index.json",
                "case_reflections.json",
                *history_files,
            ],
            "contains_repository": False,
            "contains_direct_downstream_evidence": False,
            **(
                {
                    "required_files": [
                        "counted_playbook.json",
                        "reflection_index.json",
                        *history_files[:1],
                    ],
                    "optional_files": ["case_reflections.json", *history_files[1:]],
                }
                if evidence_contract in {"distilled_v1", *ACE_PAIRED_REFLECTION_CONTRACTS}
                else {}
            ),
            **(
                {"concern_ids": concern_ids}
                if require_concern_coverage
                or require_curator_self_check
                else {}
            ),
        },
    )
    return concern_ids
