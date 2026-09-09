"""Playbook experiment semantics over the shared Slurm task transport."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.optimization.models import GEPACase
from src.optimization.playbook import RejectPlaybook, apply_refiner_operations
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.hpc.task_batch import atomic_json


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
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.executor = executor
        self.maximum_tokens = maximum_tokens
        self.token_counter = token_counter or (lambda text: len(text.split()))

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
        historical = dict(record.get("historical_evidence") or {})
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
        item = {"validation_playbook": counted.serialize(), "prompt_values": {
            "counted_internal_playbook": counted.serialize(),
            "case_reflections": json.dumps(list(reviews), ensure_ascii=False),
        }}
        return self.executor.run_wave("curator", [item])[0]["agent_output"]

    def refine(self, playbook: RejectPlaybook) -> RejectPlaybook:
        item = {"validation_playbook": playbook.serialize(), "prompt_values": {
            "internal_playbook": playbook.serialize(),
            "current_tokens": self.token_counter(playbook.render_for_checker()),
            "maximum_tokens": self.maximum_tokens,
        }}
        output = self.executor.run_wave("refiner", [item])[0]["agent_output"]
        return apply_refiner_operations(playbook, output)
