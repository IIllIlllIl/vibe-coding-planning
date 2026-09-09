"""Playbook experiment semantics over the shared Slurm task transport."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from src.optimization.models import GEPACase
from src.optimization.playbook import RejectPlaybook, apply_refiner_operations
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor


class HPCPlaybookChecker:
    def __init__(self, executor: PlaybookHPCExecutor) -> None:
        self.executor = executor

    def evaluate_batch(self, batch: Sequence[GEPACase], playbook: RejectPlaybook):
        visible = playbook.render_for_checker()
        items = [{
            "instance_id": case.instance_id,
            "prompt_values": {"issue": case.issue_description, "plan": case.plan,
                              "checker_visible_playbook": visible},
        } for case in batch]
        outputs = self.executor.run_wave("checker", items)
        return [(item["agent_output"], item["trajectory"]) for item in outputs]


class HPCPlaybookProposalAgents:
    def __init__(self, executor: PlaybookHPCExecutor, *, maximum_tokens: int) -> None:
        self.executor = executor
        self.maximum_tokens = maximum_tokens

    def reflect_batch(self, records: Sequence[Mapping[str, Any]], rounds: int):
        priors: list[Mapping[str, Any] | None] = [None] * len(records)
        for _ in range(rounds):
            items = []
            for record, prior in zip(records, priors, strict=True):
                internal = RejectPlaybook.parse(record["internal_playbook"])
                items.append({
                    "instance_id": record["instance_id"],
                    "prompt_values": {
                        "reflection_case_bundle": json.dumps(dict(record), ensure_ascii=False),
                        "internal_playbook": internal.serialize(),
                        "prior_reflection": json.dumps(prior, ensure_ascii=False) if prior else "",
                    },
                })
            priors = [item["agent_output"] for item in self.executor.run_wave("reflector", items)]
        return priors

    def curate(self, counted: RejectPlaybook, reviews: Sequence[Mapping[str, Any]]):
        item = {"prompt_values": {
            "counted_internal_playbook": counted.serialize(),
            "case_reflections": json.dumps(list(reviews), ensure_ascii=False),
        }}
        return self.executor.run_wave("curator", [item])[0]["agent_output"]

    def refine(self, playbook: RejectPlaybook) -> RejectPlaybook:
        item = {"prompt_values": {
            "internal_playbook": playbook.serialize(),
            "current_tokens": len(playbook.render_for_checker().split()),
            "maximum_tokens": self.maximum_tokens,
        }}
        output = self.executor.run_wave("refiner", [item])[0]["agent_output"]
        return apply_refiner_operations(playbook, output)
