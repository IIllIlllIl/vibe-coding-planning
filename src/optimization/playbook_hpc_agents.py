"""Playbook experiment semantics over the shared Slurm task transport."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.optimization.models import GEPACase
from src.optimization.playbook import (
    RejectPlaybook,
    apply_curator_operations,
    apply_refiner_operations,
    overlength_bullet_ids,
)
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.hpc.task_batch import TaskAttemptsExhausted, atomic_json


class HPCPlaybookChecker:
    def __init__(self, executor: PlaybookHPCExecutor) -> None:
        self.executor = executor

    def evaluate_batch(self, batch: Sequence[GEPACase], playbook: RejectPlaybook):
        visible = playbook.render_for_checker()
        items = [{
            "instance_id": case.instance_id,
            "validation_rule_count": len(playbook.bullets),
            "prompt_values": {"issue": case.issue_description, "plan": case.plan,
                              "checker_visible_playbook": visible,
                              "retry_feedback": ""},
        } for case in batch]
        outputs = self.executor.run_wave("checker", items)
        return [(item["agent_output"], item["trajectory"]) for item in outputs]


class HPCPlaybookProposalAgents:
    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        maximum_tokens: int,
        maximum_bullet_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.executor = executor
        self.maximum_tokens = maximum_tokens
        self.maximum_bullet_tokens = maximum_bullet_tokens
        self.token_counter = token_counter or (lambda text: len(text.split()))

    @staticmethod
    def _materialize_historical_evidence(
        historical: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resolve immutable raw-output references only for Reflection."""
        cache: dict[tuple[str, str], dict[str, Any]] = {}
        materialized: dict[str, Any] = {}
        for name, value in historical.items():
            if not isinstance(value, dict) or set(value) != {
                "artifact_path",
                "artifact_sha256",
                "json_field",
            }:
                materialized[name] = value
                continue
            path = Path(str(value["artifact_path"]))
            expected = str(value["artifact_sha256"])
            key = (str(path), expected)
            if key not in cache:
                payload = path.read_bytes()
                actual = hashlib.sha256(payload).hexdigest()
                if actual != expected:
                    raise ValueError(
                        f"historical evidence hash mismatch: {path}"
                    )
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError("historical evidence artifact must be an object")
                cache[key] = parsed
            field = str(value["json_field"])
            if field not in cache[key]:
                raise ValueError(
                    f"historical evidence field {field!r} is absent from {path}"
                )
            materialized[name] = cache[key][field]
        return materialized

    def _write_reflection_evidence(
        self,
        record: Mapping[str, Any],
        *,
        prior: Mapping[str, Any] | None,
    ) -> Path:
        identity = hashlib.sha256(
            json.dumps(
                {"record": record, "prior": prior},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        root = self.executor.run_dir / "reflection_evidence" / identity
        historical = self._materialize_historical_evidence(
            dict(record.get("historical_evidence") or {})
        )
        atomic_json(root / "classification.json", {
            key: record.get(key)
            for key in (
                "instance_id", "issue", "plan", "ground_truth",
                "resolved_proxy", "score", "checker_output",
                "checker_visible_playbook",
            )
        })
        atomic_json(root / "plan_trajectory.json", historical.get("plan_trajectory", []))
        atomic_json(root / "code_trajectory.json", historical.get("code_trajectory", []))
        atomic_json(root / "evaluator_result.json", historical.get("evaluator_result", {}))
        (root / "generated.patch").write_text(
            str(historical.get("generated_patch", "")), encoding="utf-8"
        )
        if prior is not None:
            atomic_json(root / "prior_reflection.json", dict(prior))
        atomic_json(root / "manifest.json", {
            "schema_version": 1,
            "instance_id": record["instance_id"],
            "files": [
                "classification.json", "plan_trajectory.json",
                "code_trajectory.json", "evaluator_result.json",
                "generated.patch",
                *(["prior_reflection.json"] if prior is not None else []),
            ],
            "contains_repository": False,
        })
        return root

    def reflect_batch(self, records: Sequence[Mapping[str, Any]], rounds: int):
        priors: list[Mapping[str, Any] | None] = [None] * len(records)
        for _ in range(rounds):
            items = []
            for record, prior in zip(records, priors, strict=True):
                internal = RejectPlaybook.parse(record["internal_playbook"])
                evidence_dir = self._write_reflection_evidence(record, prior=prior)
                items.append({
                    "instance_id": record["instance_id"],
                    "validation_playbook": internal.serialize(),
                    "evidence_dir": str(evidence_dir),
                    "prompt_values": {
                        "internal_playbook": internal.serialize(),
                        "evidence_path": "/evidence",
                    },
                })
            priors = [item["agent_output"] for item in self.executor.run_wave("reflector", items)]
        return priors

    def curate(self, counted: RejectPlaybook, reviews: Sequence[Mapping[str, Any]]):
        identity = hashlib.sha256(
            json.dumps(
                {"playbook": counted.serialize(), "reviews": list(reviews)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        evidence_dir = self.executor.run_dir / "curator_evidence" / identity
        atomic_json(evidence_dir / "counted_playbook.json", json.loads(counted.serialize()))
        atomic_json(evidence_dir / "case_reflections.json", list(reviews))
        reflection_index = []
        for review in reviews:
            summary = {
                "instance_id": review.get("instance_id"),
                "uncertainty": review.get("uncertainty"),
                "bullet_tags": review.get("bullet_tags", []),
            }
            if "reusable_concerns" in review:
                summary["reusable_concerns"] = review["reusable_concerns"]
            else:
                summary["key_insight"] = review.get("key_insight")
            reflection_index.append(summary)
        atomic_json(evidence_dir / "reflection_index.json", reflection_index)
        atomic_json(evidence_dir / "manifest.json", {
            "schema_version": 1,
            "case_count": len(reviews),
            "files": [
                "counted_playbook.json",
                "reflection_index.json",
                "case_reflections.json",
            ],
            "contains_repository": False,
            "contains_direct_downstream_evidence": False,
        })
        item = {
            "validation_playbook": counted.serialize(),
            "evidence_dir": str(evidence_dir),
            "prompt_values": {
                "counted_internal_playbook": counted.serialize(),
                "case_count": len(reviews),
                "evidence_path": "/evidence",
            },
        }
        try:
            return self.executor.run_wave("curator", [item])[0]["agent_output"]
        except TaskAttemptsExhausted:
            # Length-invalid Curator outputs are method candidates, not
            # operationally incomplete cases. Recover the last durable Agent
            # completion only when it is structurally valid and its sole
            # remaining defect is the configured per-bullet cap. Evaluation
            # then assigns the frozen INVALID score (-100).
            if self.maximum_bullet_tokens is None:
                raise
            batch_dir = self.executor.batch_dir_for("curator", [item])
            completions = sorted(
                (batch_dir / "attempts" / "task_0000").glob(
                    "attempt_*/agent_completion.json"
                )
            )
            if not completions:
                raise
            completion = json.loads(completions[-1].read_text(encoding="utf-8"))
            output = completion.get("agent_output")
            proposed = apply_curator_operations(counted, output)
            invalid = overlength_bullet_ids(
                proposed,
                token_counter=self.token_counter,
                maximum_bullet_tokens=self.maximum_bullet_tokens,
            )
            if not invalid:
                raise
            return output

    def refine(self, playbook: RejectPlaybook) -> RejectPlaybook:
        item = {"validation_playbook": playbook.serialize(), "prompt_values": {
            "internal_playbook": playbook.serialize(),
            "current_tokens": self.token_counter(playbook.render_for_checker()),
            "maximum_tokens": self.maximum_tokens,
        }}
        output = self.executor.run_wave("refiner", [item])[0]["agent_output"]
        return apply_refiner_operations(playbook, output)
