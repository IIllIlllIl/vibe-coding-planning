"""Deterministic contracts for the Offline reject-playbook method."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Any, Callable, Iterable, Mapping, Sequence


PLAYBOOK_SCHEMA_VERSION = 1
MAX_VISIBLE_TOKENS = 10_000
_BULLET_ID = re.compile(r"^[a-z][a-z0-9-]*-[0-9]{5}$")


@dataclass(frozen=True)
class PlaybookBullet:
    id: str
    text: str
    helpful: int = 0
    harmful: int = 0
    lineage: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Any) -> "PlaybookBullet":
        if not isinstance(value, dict) or set(value) != {
            "id", "text", "helpful", "harmful", "lineage"
        }:
            raise ValueError("playbook bullet has an invalid schema")
        bullet_id = value["id"]
        text = value["text"]
        helpful = value["helpful"]
        harmful = value["harmful"]
        lineage = value["lineage"]
        if not isinstance(bullet_id, str) or not _BULLET_ID.fullmatch(bullet_id):
            raise ValueError("playbook bullet ID is invalid")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("playbook bullet text must be non-empty")
        if (
            not isinstance(helpful, int)
            or isinstance(helpful, bool)
            or helpful < 0
            or not isinstance(harmful, int)
            or isinstance(harmful, bool)
            or harmful < 0
        ):
            raise ValueError("playbook counters must be non-negative integers")
        if (
            not isinstance(lineage, list)
            or any(not isinstance(item, str) or not item for item in lineage)
            or len(lineage) != len(set(lineage))
        ):
            raise ValueError("playbook lineage must contain unique strings")
        return cls(bullet_id, text.strip(), helpful, harmful, tuple(lineage))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "helpful": self.helpful,
            "harmful": self.harmful,
            "lineage": list(self.lineage),
        }


@dataclass(frozen=True)
class RejectPlaybook:
    bullets: tuple[PlaybookBullet, ...]
    schema_version: int = PLAYBOOK_SCHEMA_VERSION

    @classmethod
    def parse(cls, value: str | Mapping[str, Any]) -> "RejectPlaybook":
        raw = json.loads(value) if isinstance(value, str) else dict(value)
        if set(raw) != {"schema_version", "bullets"}:
            raise ValueError("playbook has unexpected or missing keys")
        if raw["schema_version"] != PLAYBOOK_SCHEMA_VERSION:
            raise ValueError("unsupported playbook schema version")
        if not isinstance(raw["bullets"], list):
            raise ValueError("playbook bullets must be a list")
        bullets = tuple(PlaybookBullet.from_dict(item) for item in raw["bullets"])
        ids = [item.id for item in bullets]
        if len(ids) != len(set(ids)):
            raise ValueError("playbook bullet IDs must be unique")
        texts = [" ".join(item.text.casefold().split()) for item in bullets]
        if len(texts) != len(set(texts)):
            raise ValueError("playbook bullet texts must be unique")
        return cls(bullets)

    def serialize(self) -> str:
        return json.dumps(
            {
                "schema_version": self.schema_version,
                "bullets": [item.to_dict() for item in self.bullets],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    def render_for_checker(self) -> str:
        lines = ["Reject the plan when:"]
        lines.extend(
            f"Rule {index}. {bullet.text}"
            for index, bullet in enumerate(self.bullets, start=1)
        )
        return "\n\n".join(lines)


@dataclass(frozen=True)
class RuleResult:
    rule_number: int
    triggered: bool
    plan_evidence: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_number": self.rule_number,
            "triggered": self.triggered,
            "plan_evidence": list(self.plan_evidence),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PlaybookCheckerOutput:
    rule_results: tuple[RuleResult, ...]
    rejected: bool
    trajectory: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, include_trajectory: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule_results": [item.to_dict() for item in self.rule_results],
            "derived_decision": "REJECT" if self.rejected else "ACCEPT",
        }
        if include_trajectory:
            result["trajectory"] = list(self.trajectory)
        return result


def validate_checker_result(
    value: Any,
    playbook: RejectPlaybook,
    *,
    trajectory: Sequence[Mapping[str, Any]] = (),
) -> PlaybookCheckerOutput:
    if not isinstance(value, dict) or set(value) != {"rule_results"}:
        raise ValueError("Checker output must contain only rule_results")
    rows = value["rule_results"]
    if not isinstance(rows, list) or len(rows) != len(playbook.bullets):
        raise ValueError("Checker must return one result per playbook bullet")
    parsed = []
    for expected_number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "rule_number", "triggered", "plan_evidence", "reason"
        }:
            raise ValueError("Checker rule result has an invalid schema")
        if row["rule_number"] != expected_number:
            raise ValueError("Checker rule results must use every ordinal in order")
        if not isinstance(row["triggered"], bool):
            raise ValueError("Checker triggered must be boolean")
        evidence = row["plan_evidence"]
        if not isinstance(evidence, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence
        ):
            raise ValueError("Checker plan_evidence must be non-empty strings")
        if row["triggered"] and not evidence:
            raise ValueError("a triggered rule requires Plan evidence")
        reason = row["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Checker rule reason must be non-empty")
        parsed.append(
            RuleResult(
                expected_number,
                row["triggered"],
                tuple(item.strip() for item in evidence),
                reason.strip(),
            )
        )
    return PlaybookCheckerOutput(
        tuple(parsed),
        rejected=any(item.triggered for item in parsed),
        trajectory=tuple(dict(item) for item in trajectory),
    )


def classification_cost(*, resolved: bool, rejected: bool) -> float:
    """Return the frozen cost-sensitive score; higher is better."""
    if rejected:
        return -5.0 if resolved else 0.0
    return 0.0 if resolved else -1.0


def invalid_score() -> float:
    return -100.0


_REFLECTOR_TAGS = frozenset({"helpful", "neutral", "harmful"})


def validate_reflector_review(
    value: Any,
    *,
    instance_id: str,
    playbook: RejectPlaybook,
) -> dict[str, Any]:
    required = {
        "instance_id", "reasoning", "error_identification",
        "root_cause_analysis", "correct_approach", "key_insight",
        "bullet_tags", "uncertainty",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Reflector review has an invalid schema")
    if value["instance_id"] != instance_id:
        raise ValueError("Reflector instance ID mismatch")
    for key in required - {"instance_id", "bullet_tags"}:
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"Reflector {key} must be non-empty")
    tags = value["bullet_tags"]
    if not isinstance(tags, list) or len(tags) != len(playbook.bullets):
        raise ValueError("Reflector must tag every active bullet")
    expected_ids = [item.id for item in playbook.bullets]
    normalized = []
    for expected_id, tag in zip(expected_ids, tags, strict=True):
        if not isinstance(tag, dict) or set(tag) != {
            "id", "tag", "attribution", "confidence"
        }:
            raise ValueError("Reflector bullet tag has an invalid schema")
        if tag["id"] != expected_id or tag["tag"] not in _REFLECTOR_TAGS:
            raise ValueError("Reflector bullet tag identity or value is invalid")
        if tag["confidence"] not in {"low", "medium", "high"}:
            raise ValueError("Reflector confidence is invalid")
        if not isinstance(tag["attribution"], str) or not tag["attribution"].strip():
            raise ValueError("Reflector attribution must be non-empty")
        normalized.append(dict(tag))
    result = dict(value)
    result["bullet_tags"] = normalized
    return result


def apply_reflector_counters(
    playbook: RejectPlaybook,
    reviews: Iterable[Mapping[str, Any]],
) -> RejectPlaybook:
    deltas = {item.id: {"helpful": 0, "harmful": 0} for item in playbook.bullets}
    for review in reviews:
        for tag in review["bullet_tags"]:
            if tag["tag"] in {"helpful", "harmful"}:
                deltas[tag["id"]][tag["tag"]] += 1
    return RejectPlaybook(
        tuple(
            replace(
                item,
                helpful=item.helpful + deltas[item.id]["helpful"],
                harmful=item.harmful + deltas[item.id]["harmful"],
            )
            for item in playbook.bullets
        )
    )


def _next_bullet_id(playbook: RejectPlaybook, reserved: set[str]) -> str:
    numbers = [
        int(item.id.rsplit("-", 1)[1])
        for item in playbook.bullets
        if item.id.startswith("plan-")
    ]
    number = max(numbers, default=0) + 1
    while f"plan-{number:05d}" in reserved:
        number += 1
    bullet_id = f"plan-{number:05d}"
    reserved.add(bullet_id)
    return bullet_id


def apply_curator_operations(
    playbook: RejectPlaybook,
    value: Any,
) -> RejectPlaybook:
    """Validate and deterministically apply Curator delta operations."""
    if not isinstance(value, dict) or set(value) != {"reasoning", "operations"}:
        raise ValueError("Curator output must contain reasoning and operations")
    if not isinstance(value["reasoning"], str) or not value["reasoning"].strip():
        raise ValueError("Curator reasoning must be non-empty")
    operations = value["operations"]
    if not isinstance(operations, list):
        raise ValueError("Curator operations must be a list")
    original = {item.id: item for item in playbook.bullets}
    targeted: set[str] = set()
    parsed: list[tuple[str, tuple[str, ...], str | None]] = []
    for operation in operations:
        if not isinstance(operation, dict) or "type" not in operation:
            raise ValueError("Curator operation has an invalid schema")
        kind = operation["type"]
        common = {"type", "supporting_instance_ids", "risk_analysis"}
        if kind == "ADD":
            expected = common | {"content"}
            targets: tuple[str, ...] = ()
        elif kind in {"REVISE", "DELETE"}:
            expected = common | {"target_id"} | ({"content"} if kind == "REVISE" else set())
            targets = (operation.get("target_id"),)
        elif kind == "MERGE":
            expected = common | {"target_ids", "content"}
            raw_targets = operation.get("target_ids")
            if not isinstance(raw_targets, list) or len(raw_targets) < 2:
                raise ValueError("MERGE requires at least two target IDs")
            targets = tuple(raw_targets)
        else:
            raise ValueError(f"unsupported Curator operation: {kind!r}")
        if set(operation) != expected:
            raise ValueError("Curator operation has unexpected or missing keys")
        supporting = operation["supporting_instance_ids"]
        if not isinstance(supporting, list) or not supporting or any(
            not isinstance(item, str) or not item for item in supporting
        ):
            raise ValueError("Curator operation requires supporting instances")
        if not isinstance(operation["risk_analysis"], str) or not operation["risk_analysis"].strip():
            raise ValueError("Curator operation requires risk analysis")
        for target in targets:
            if target not in original:
                raise ValueError("Curator operation targets an unknown bullet")
            if target in targeted:
                raise ValueError("Curator operations target one bullet more than once")
            targeted.add(target)
        content = operation.get("content")
        if content is not None and (not isinstance(content, str) or not content.strip()):
            raise ValueError("Curator operation content must be non-empty")
        parsed.append((kind, targets, content.strip() if content else None))

    retained = [item for item in playbook.bullets if item.id not in targeted]
    reserved = set(original)
    for kind, targets, content in parsed:
        if kind in {"ADD", "REVISE", "MERGE"}:
            retained.append(
                PlaybookBullet(
                    _next_bullet_id(playbook, reserved),
                    content or "",
                    lineage=targets,
                )
            )
    return RejectPlaybook.parse(
        {"schema_version": PLAYBOOK_SCHEMA_VERSION, "bullets": [item.to_dict() for item in retained]}
    )


def apply_refiner_operations(playbook: RejectPlaybook, value: Any) -> RejectPlaybook:
    """Apply semantic REWORD/MERGE operations without allowing value pruning."""
    if not isinstance(value, dict) or set(value) != {"reasoning", "operations"}:
        raise ValueError("Refiner output must contain reasoning and operations")
    if not isinstance(value["reasoning"], str) or not value["reasoning"].strip():
        raise ValueError("Refiner reasoning must be non-empty")
    if not isinstance(value["operations"], list):
        raise ValueError("Refiner operations must be a list")
    original = {item.id: item for item in playbook.bullets}
    targeted: set[str] = set()
    operations = []
    for operation in value["operations"]:
        if not isinstance(operation, dict) or operation.get("type") not in {"REWORD", "MERGE"}:
            raise ValueError("unsupported Refiner operation")
        kind = operation["type"]
        expected = {"type", "content", "target_id" if kind == "REWORD" else "target_ids"}
        if set(operation) != expected:
            raise ValueError("Refiner operation has an invalid schema")
        raw_targets = [operation["target_id"]] if kind == "REWORD" else operation["target_ids"]
        if not isinstance(raw_targets, list) or (kind == "MERGE" and len(raw_targets) < 2):
            raise ValueError("Refiner targets are invalid")
        targets = tuple(raw_targets)
        if any(target not in original or target in targeted for target in targets):
            raise ValueError("Refiner target is unknown or repeated")
        targeted.update(targets)
        content = operation["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Refiner content must be non-empty")
        operations.append((kind, targets, content.strip()))
    retained = [item for item in playbook.bullets if item.id not in targeted]
    reserved = set(original)
    for kind, targets, content in operations:
        if kind == "REWORD":
            old = original[targets[0]]
            retained.append(replace(old, text=content))
        else:
            retained.append(PlaybookBullet(_next_bullet_id(playbook, reserved), content, lineage=targets))
    return RejectPlaybook.parse({"schema_version": 1, "bullets": [item.to_dict() for item in retained]})


def validate_curator_proposal(
    counted: RejectPlaybook,
    proposed: RejectPlaybook,
) -> RejectPlaybook:
    """Keep counter arithmetic host-owned across semantic curation.

    Retaining an ID means retaining that bullet verbatim.  Revised and merged
    rules therefore receive a new ID and start with zero counters; their
    ancestry belongs in ``lineage``.
    """
    existing = {item.id: item for item in counted.bullets}
    for bullet in proposed.bullets:
        previous = existing.get(bullet.id)
        if previous is not None and bullet != previous:
            raise ValueError("Curator may not modify a retained bullet ID")
        if previous is None and (bullet.helpful != 0 or bullet.harmful != 0):
            raise ValueError("new Curator bullets must start with zero counters")
        if any(parent not in existing for parent in bullet.lineage):
            raise ValueError("Curator lineage must reference an input bullet ID")
    return proposed


def validate_refiner_proposal(
    source: RejectPlaybook,
    refined: RejectPlaybook,
) -> RejectPlaybook:
    """Prevent the semantic length Refiner from manufacturing evidence."""
    existing = {item.id: item for item in source.bullets}
    for bullet in refined.bullets:
        previous = existing.get(bullet.id)
        if previous is not None and (
            bullet.helpful != previous.helpful
            or bullet.harmful != previous.harmful
            or bullet.lineage != previous.lineage
        ):
            raise ValueError("Refiner may not modify counters or lineage")
        if previous is None:
            if bullet.helpful != 0 or bullet.harmful != 0:
                raise ValueError("new Refiner bullets must start with zero counters")
            if not bullet.lineage or any(
                parent not in existing for parent in bullet.lineage
            ):
                raise ValueError(
                    "merged Refiner bullets require input-bullet lineage"
                )
    return refined


TokenCounter = Callable[[str], int]
SemanticRefiner = Callable[[RejectPlaybook], RejectPlaybook]


def manage_playbook_length(
    playbook: RejectPlaybook,
    *,
    token_counter: TokenCounter,
    semantic_refiner: SemanticRefiner | None,
    maximum_tokens: int = MAX_VISIBLE_TOKENS,
) -> tuple[RejectPlaybook, dict[str, Any]]:
    """Refine once, then deterministically prune whole bullets if required."""
    if maximum_tokens < 1:
        raise ValueError("maximum_tokens must be positive")
    before = token_counter(playbook.render_for_checker())
    refined = False
    current = playbook
    if before > maximum_tokens:
        if semantic_refiner is None:
            raise ValueError("overlength playbook requires a semantic refiner")
        current = semantic_refiner(playbook)
        if not isinstance(current, RejectPlaybook):
            raise ValueError("semantic refiner must return a RejectPlaybook")
        current = RejectPlaybook.parse(current.serialize())
        current = validate_refiner_proposal(playbook, current)
        refined = True
    removed: list[str] = []
    while token_counter(current.render_for_checker()) > maximum_tokens:
        if not current.bullets:
            raise ValueError("playbook header alone exceeds the token limit")
        # False rejection costs five times false acceptance. Lowest supported
        # utility is removed first; ties are stable and favor shorter output.
        victim = min(
            current.bullets,
            key=lambda item: (
                item.helpful - 5 * item.harmful,
                -item.harmful,
                item.helpful,
                -token_counter(item.text),
                item.id,
            ),
        )
        removed.append(victim.id)
        current = RejectPlaybook(
            tuple(item for item in current.bullets if item.id != victim.id)
        )
    return current, {
        "maximum_tokens": maximum_tokens,
        "tokens_before": before,
        "tokens_after": token_counter(current.render_for_checker()),
        "semantic_refiner_ran": refined,
        "deterministically_removed_ids": removed,
    }
