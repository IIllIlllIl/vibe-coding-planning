"""Incremental hybrid maintenance using the existing ACE ledger and operations."""
import json
import hashlib
from pathlib import Path

from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.playbook import (
    PlaybookBullet, RejectPlaybook, apply_curator_operations,
)
from src.optimization.paired_playbook import validate_ace_paired_reflector_review
from src.optimization.playbook_adapter import GlobalPlaybookCounters
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.audit import redact_sensitive
from src.exceptions import ControllerYield


def _content(condition, guide, shared):
    # Complete effective meaning, not branch-local IDs, identifies attribution.
    content = {"condition": condition, "guide": guide, "shared_guide": shared}
    identity = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return json.dumps(dict(content, identity_sha256=identity), ensure_ascii=False, sort_keys=True)


def _playbook(artifact):
    return RejectPlaybook.parse({"schema_version": 1, "bullets": [
        PlaybookBullet(item["id"], _content(item["condition"], item["guide"],
                                           artifact["shared_guide"])).to_dict()
        for item in artifact["checklist"]]})


def apply_hybrid_delta(parent, delta, records, limits, *, counted=None, history=None):
    """Validate and mechanically apply one Agent-authored hybrid transaction."""
    if not isinstance(delta, dict) or set(delta) != {"reasoning", "operations", "shared_guide"}:
        raise ValueError("Curator fields: reasoning, operations, shared_guide")
    if not isinstance(delta["operations"], list):
        raise ValueError("Curator operations must be an array")
    operations = []
    for raw in delta["operations"]:
        if not isinstance(raw, dict):
            raise ValueError("Curator operation must be an object")
        op = dict(raw)
        if op.get("type") not in {"ADD", "UPDATE", "MERGE", "REMOVE"}:
            raise ValueError("supported operations: ADD, UPDATE, MERGE, REMOVE")
        if op["type"] != "REMOVE":
            content = op.get("content")
            if not isinstance(content, dict) or set(content) != {"condition", "guide"}:
                raise ValueError("content fields: condition, guide")
            # Type/word validation before encoding prevents JSON strings hiding
            # non-string Agent fields until after the mechanical operation.
            validate_candidate(json.dumps({"schema_version": 1, "shared_guide": delta["shared_guide"],
                "checklist": [{"id": "plan-00001", **content}]}), limits)
            op["content"] = _content(content["condition"], content["guide"], parent["shared_guide"])
        ids = op.get("supporting_instance_ids")
        historical_remove = op["type"] == "REMOVE" and bool(history and history.get("events"))
        if not isinstance(ids, list) or (not ids and not historical_remove) or any(
            not isinstance(identifier, str) or identifier not in {r["instance_id"] for r in records}
            for identifier in ids
        ):
            raise ValueError("supporting IDs must identify supplied training records")
        operations.append(op)
    updated = apply_curator_operations(counted or _playbook(parent),
                                      {"reasoning": delta["reasoning"], "operations": operations},
                                      allow_history_only_remove=bool(history and history.get("events")))
    items = []
    for bullet in updated.bullets:
        content = json.loads(bullet.text)
        items.append({"id": bullet.id, "condition": content["condition"], "guide": content["guide"]})
    proposed = json.dumps({"schema_version": 1, "shared_guide": delta["shared_guide"], "checklist": items}, ensure_ascii=False)
    validate_candidate(proposed, limits)
    return proposed


class IncrementalHybridACEProposer:
    """Curator receives counted parent, full training history and reviews.

    Reflector callback: (artifact, record) -> (review, trajectory).
    Curator callback: (artifact, reviews, records, history, limits) -> (delta, trajectory).
    Only accepted, fully validated proposals commit attribution to the ledger.
    """
    def __init__(self, reflector, curator, limits, *, ledger_path: Path | None = None,
                 trace_dir: Path | None = None, refiner=None):
        self.reflector, self.curator, self.limits = reflector, curator, limits
        self.ledger = GlobalPlaybookCounters(ledger_path)
        self.successful_proposals = self.proposal_failures = 0
        self.last_trace = None
        self.trace_dir = trace_dir
        self.refiner = refiner
        self.failures = []

    def __call__(self, candidate, reflective_dataset, components_to_update):
        if components_to_update != ["rules"]:
            raise ValueError("only rules may be updated")
        parent = validate_candidate(candidate["rules"], self.limits)
        records = list(reflective_dataset["rules"])
        if not records or any(row.get("split") != "train" for row in records):
            raise ValueError("ACE accepts training evidence only")
        if len({row["instance_id"] for row in records}) != len(records):
            raise ValueError("training records must have unique instance IDs")
        book = self.ledger.hydrate(_playbook(parent))
        self.last_trace = {"parent": parent, "records": records, "reflector": []}
        trace_path = None
        if self.trace_dir is not None:
            identity = hashlib.sha256(json.dumps({"parent": parent, "records": records}, sort_keys=True).encode()).hexdigest()
            trace_path = self.trace_dir / f"{identity}.json"
        def persist(status):
            if trace_path is not None:
                atomic_json(trace_path, redact_sensitive(dict(self.last_trace, status=status)))
        try:
            reviews = []
            for record in records:
                review, trajectory = self.reflector(parent, record)
                self.last_trace["reflector"].append({"review": review, "trajectory": trajectory})
                persist("reflecting")
                reviews.append(validate_ace_paired_reflector_review(review, instance_id=record["instance_id"],
                                                                  playbook=book, output_contract="ace_v2"))
            counted, pending = self.ledger.apply_reviews(book, reviews)
            history = self.ledger.feedback_for(book, reviews, records, include_inactive=True)
            curator_parent = dict(parent, counters=[
                {"id": b.id, "helpful": b.helpful, "harmful": b.harmful, "neutral": b.neutral}
                for b in counted.bullets])
            delta, trajectory = self.curator(curator_parent, reviews, records, history, vars(self.limits))
            self.last_trace.update(curator_delta=delta, curator_trajectory=trajectory, history=history)
            persist("validating")
            if self.refiner is not None:
                # Curator must submit a structurally valid proposal first. Only
                # size violations authorize a separate Agent-authored refinement.
                from .behavioral_hybrid import HybridLimits
                relaxed = HybridLimits(1000000, 1000000, 1000000, 1000000)
                draft = apply_hybrid_delta(parent, delta, records, relaxed, counted=counted, history=history)
                try:
                    validate_candidate(draft, self.limits)
                except ValueError:
                    refined, refinement_trace = self.refiner(json.loads(draft), vars(self.limits))
                    self.last_trace.update(refiner_output=refined, refiner_trajectory=refinement_trace)
                    persist("refining")
                    proposed = json.dumps(refined, ensure_ascii=False)
                    validate_candidate(proposed, self.limits)
                else:
                    proposed = draft
            else:
                proposed = apply_hybrid_delta(parent, delta, records, self.limits, counted=counted, history=history)
        except ControllerYield:
            persist("yielded")
            raise
        except Exception as exc:
            self.failures.append({"error_type": type(exc).__name__, "error": str(exc)})
            self.last_trace["error"] = str(exc)
            persist("failed")
            self.proposal_failures += 1
            raise
        self.ledger.commit(counted, pending, feedback=history)
        self.successful_proposals += 1
        self.last_trace["candidate"] = proposed
        persist("completed")
        return {"rules": proposed}
