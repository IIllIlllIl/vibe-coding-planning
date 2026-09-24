"""Playbook experiment semantics over the shared Slurm task transport."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.optimization.models import GEPACase, PairedGEPACase
from src.optimization.playbook import (
    RejectPlaybook,
    apply_curator_operations,
    apply_refiner_operations,
    overlength_bullet_ids,
    validate_curator_concern_coverage,
    validate_curator_self_check,
)
from src.optimization.repo_playbook import render_concern_playbook
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.hpc.task_batch import TaskAttemptsExhausted, atomic_json
from src.evaluator.swe_evaluator import derive_image_name


def _image_authority(
    case: GEPACase,
    records: Mapping[str, Any],
) -> dict[str, Any]:
    repository = {
        "repo": case.repository.repo,
        "base_commit": case.repository.base_commit,
        "instance_id": case.repository.instance_id,
    }
    return _image_authority_for_repository(case.instance_id, repository, records)


def _image_authority_for_repository(
    instance_id: str,
    repository: Mapping[str, Any],
    records: Mapping[str, Any],
) -> dict[str, Any]:
    if repository.get("instance_id") != instance_id:
        raise ValueError(f"{instance_id}: repository identity mismatch")
    image_ref = derive_image_name(repository)
    raw = records.get(image_ref)
    if not isinstance(raw, dict):
        raise ValueError(f"{instance_id}: Repo Checker image authority is missing")
    if raw.get("instance_id") != instance_id:
        raise ValueError(f"{instance_id}: Repo Checker image identity mismatch")
    if raw.get("status") not in {"cached", "pulled", "audited"}:
        raise ValueError(f"{instance_id}: Repo Checker image is unavailable")
    if raw.get("base_commit_verified") is not True:
        raise ValueError(f"{instance_id}: Repo Checker base commit is unverified")
    if raw.get("expected_base_commit") != repository.get("base_commit"):
        raise ValueError(f"{instance_id}: Repo Checker base commit authority mismatch")
    required = ("sif_path", "sif_sha256", "sif_bytes")
    if any(raw.get(key) in {None, ""} for key in required):
        raise ValueError(f"{instance_id}: Repo Checker SIF authority is incomplete")
    sif_hash = str(raw["sif_sha256"])
    if len(sif_hash) != 64 or any(
        value not in "0123456789abcdef" for value in sif_hash
    ):
        raise ValueError(f"{instance_id}: Repo Checker SIF hash is invalid")
    if int(raw["sif_bytes"]) <= 0:
        raise ValueError(f"{instance_id}: Repo Checker SIF size is invalid")
    return {
        "requested_ref": image_ref,
        "sif_path": str(raw["sif_path"]),
        "sif_sha256": sif_hash,
        "sif_bytes": int(raw["sif_bytes"]),
    }


class HPCPlaybookChecker:
    def __init__(self, executor: PlaybookHPCExecutor) -> None:
        self.executor = executor

    def evaluate_batch(self, batch: Sequence[GEPACase], playbook: RejectPlaybook):
        visible = playbook.render_for_checker()
        items = [
            {
                "instance_id": case.instance_id,
                "validation_rule_count": len(playbook.bullets),
                "prompt_values": {
                    "issue": case.issue_description,
                    "plan": case.plan,
                    "checker_visible_playbook": visible,
                    "retry_feedback": "",
                },
            }
            for case in batch
        ]
        outputs = self.executor.run_wave("checker", items)
        return [(item["agent_output"], item["trajectory"]) for item in outputs]


class HPCRepoPlaybookChecker:
    """Repository-aware Checker using the same one-Agent Slurm transport."""

    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        image_records: Mapping[str, Any],
    ) -> None:
        self.executor = executor
        self.image_records = image_records

    def evaluate_batch(self, batch: Sequence[GEPACase], playbook: RejectPlaybook):
        visible = render_concern_playbook(playbook)
        items = []
        for case in batch:
            repository = {
                "repo": case.repository.repo,
                "base_commit": case.repository.base_commit,
                "instance_id": case.repository.instance_id,
            }
            items.append(
                {
                    "instance_id": case.instance_id,
                    "validation_rule_count": len(playbook.bullets),
                    "repository": repository,
                    "image_authority": _image_authority(case, self.image_records),
                    "prompt_values": {
                        "issue": case.issue_description,
                        "plan": case.plan,
                        "checker_visible_playbook": visible,
                        "retry_feedback": "",
                    },
                }
            )
        outputs = self.executor.run_wave("repo_checker", items)
        return [(item["agent_output"], item["trajectory"]) for item in outputs]


class HPCPairedRepoPlaybookChecker:
    """Run two label-blind Checker tasks for every within-task Plan pair."""

    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        image_records: Mapping[str, Any],
        levels: bool = False,
    ) -> None:
        self.executor = executor
        self.image_records = image_records
        self.levels = levels

    def evaluate_batch(
        self,
        batch: Sequence[PairedGEPACase],
        playbook: RejectPlaybook,
    ):
        visible = render_concern_playbook(playbook)
        items = []
        for case in batch:
            repository = {
                "repo": case.repository.repo,
                "base_commit": case.repository.base_commit,
                "instance_id": case.repository.instance_id,
            }
            authority = _image_authority_for_repository(
                case.task_id, repository, self.image_records
            )
            for observation in (
                case.resolved_observation,
                case.unresolved_observation,
            ):
                items.append(
                    {
                        # This transport identity is never interpolated into the
                        # prompt. It contains neither the pair ID nor side label.
                        "instance_id": observation.observation_id,
                        **({"output_contract": "levels_v1"} if self.levels else {}),
                        "validation_rule_count": len(playbook.bullets),
                        "repository": repository,
                        "image_authority": authority,
                        "prompt_values": {
                            "issue": case.issue_description,
                            "plan": observation.plan,
                            "checker_visible_playbook": visible,
                            "retry_feedback": "",
                        },
                    }
                )
        outputs = self.executor.run_wave("paired_repo_checker", items)
        results = [(item["agent_output"], item["trajectory"]) for item in outputs]
        return [
            (results[index], results[index + 1]) for index in range(0, len(results), 2)
        ]


class HPCPlaybookProposalAgents:
    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        maximum_tokens: int,
        maximum_bullet_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
        visible_renderer: Callable[[RejectPlaybook], str] | None = None,
        require_concern_coverage: bool = False,
        evidence_contract: str = "legacy_v1",
        require_curator_self_check: bool = False,
    ) -> None:
        self.executor = executor
        self.maximum_tokens = maximum_tokens
        self.maximum_bullet_tokens = maximum_bullet_tokens
        self.token_counter = token_counter or (lambda text: len(text.split()))
        self.visible_renderer = visible_renderer or (
            lambda value: value.render_for_checker()
        )
        self.require_concern_coverage = require_concern_coverage
        if evidence_contract not in {"legacy_v1", "distilled_v1", "ace_v1"}:
            raise ValueError("unknown Curator evidence contract")
        if evidence_contract == "distilled_v1" and require_concern_coverage:
            raise ValueError(
                "distilled Curator evidence cannot require finding dispositions"
            )
        self.evidence_contract = evidence_contract
        self.require_curator_self_check = require_curator_self_check

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
                    raise ValueError(f"historical evidence hash mismatch: {path}")
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
        include_repository_reference: bool = False,
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
        classification_keys = [
            "instance_id",
            "issue",
            "plan",
            "ground_truth",
            "resolved_proxy",
            "score",
            "checker_output",
            "checker_visible_playbook",
        ]
        if include_repository_reference:
            classification_keys.append("repository")
        atomic_json(
            root / "classification.json",
            {key: record.get(key) for key in classification_keys},
        )
        atomic_json(
            root / "plan_trajectory.json", historical.get("plan_trajectory", [])
        )
        atomic_json(
            root / "code_trajectory.json", historical.get("code_trajectory", [])
        )
        atomic_json(
            root / "evaluator_result.json", historical.get("evaluator_result", {})
        )
        (root / "generated.patch").write_text(
            str(historical.get("generated_patch", "")), encoding="utf-8"
        )
        if prior is not None:
            atomic_json(root / "prior_reflection.json", dict(prior))
        manifest = {
            "schema_version": 1,
            "instance_id": record["instance_id"],
            "files": [
                "classification.json",
                "plan_trajectory.json",
                "code_trajectory.json",
                "evaluator_result.json",
                "generated.patch",
                *(["prior_reflection.json"] if prior is not None else []),
            ],
            "contains_repository": False,
        }
        if include_repository_reference:
            manifest["contains_repository_reference"] = True
        atomic_json(root / "manifest.json", manifest)
        return root

    def reflect_batch(self, records: Sequence[Mapping[str, Any]], rounds: int):
        priors: list[Mapping[str, Any] | None] = [None] * len(records)
        for _ in range(rounds):
            items = []
            for record, prior in zip(records, priors, strict=True):
                internal = RejectPlaybook.parse(record["internal_playbook"])
                evidence_dir = self._write_reflection_evidence(record, prior=prior)
                items.append(
                    {
                        "instance_id": record["instance_id"],
                        "validation_playbook": internal.serialize(),
                        "evidence_dir": str(evidence_dir),
                        "prompt_values": {
                            "internal_playbook": internal.serialize(),
                            "evidence_path": "/evidence",
                        },
                    }
                )
            priors = [
                item["agent_output"]
                for item in self.executor.run_wave("reflector", items)
            ]
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
        atomic_json(
            evidence_dir / "counted_playbook.json", json.loads(counted.serialize())
        )
        atomic_json(evidence_dir / "case_reflections.json", list(reviews))
        reflection_index = []
        concern_ids: list[str] = []
        for review in reviews:
            if self.evidence_contract == "ace_v1":
                numbered = []
                for index, insight in enumerate(
                    review.get("key_insights", []), start=1
                ):
                    concern_id = f'{review["instance_id"]}:c{index}'
                    concern_ids.append(concern_id)
                    numbered.append({"id": concern_id, **dict(insight)})
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
            if self.evidence_contract == "distilled_v1":
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
                if self.require_concern_coverage:
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
                ],
                "contains_repository": False,
                "contains_direct_downstream_evidence": False,
                **(
                    {
                        "required_files": [
                            "counted_playbook.json",
                            "reflection_index.json",
                        ],
                        "optional_files": ["case_reflections.json"],
                    }
                    if self.evidence_contract in {"distilled_v1", "ace_v1"}
                    else {}
                ),
                **(
                    {"concern_ids": concern_ids}
                    if self.require_concern_coverage
                    or self.require_curator_self_check
                    else {}
                ),
            },
        )
        item = {
            "validation_playbook": counted.serialize(),
            "evidence_dir": str(evidence_dir),
            "prompt_values": {
                "counted_internal_playbook": counted.serialize(),
                "case_count": len(reviews),
                "evidence_path": "/evidence",
            },
            **({"validation_concern_ids": concern_ids} if self.require_concern_coverage else {}),
            **(
                {"validation_self_check_concern_ids": concern_ids}
                if self.require_curator_self_check
                else {}
            ),
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
            if self.require_concern_coverage:
                validate_curator_concern_coverage(output, concern_ids)
            if self.require_curator_self_check:
                validate_curator_self_check(output, concern_ids)
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
        item = {
            "validation_playbook": playbook.serialize(),
            "prompt_values": {
                "internal_playbook": playbook.serialize(),
                "current_tokens": self.token_counter(self.visible_renderer(playbook)),
                "maximum_tokens": self.maximum_tokens,
            },
        }
        output = self.executor.run_wave("refiner", [item])[0]["agent_output"]
        return apply_refiner_operations(playbook, output)


class HPCRepoPlaybookProposalAgents(HPCPlaybookProposalAgents):
    """Repo-aware per-case Reflection with shared Curator and Refiner."""

    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        image_records: Mapping[str, Any],
        maximum_tokens: int,
        maximum_bullet_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        super().__init__(
            executor,
            maximum_tokens=maximum_tokens,
            maximum_bullet_tokens=maximum_bullet_tokens,
            token_counter=token_counter,
            visible_renderer=render_concern_playbook,
        )
        self.image_records = image_records

    def reflect_batch(self, records: Sequence[Mapping[str, Any]], rounds: int):
        priors: list[Mapping[str, Any] | None] = [None] * len(records)
        for _ in range(rounds):
            items = []
            for record, prior in zip(records, priors, strict=True):
                internal = RejectPlaybook.parse(record["internal_playbook"])
                repository = record.get("repository")
                if not isinstance(repository, dict):
                    raise ValueError("Repo Reflection record lacks repository identity")
                evidence_dir = self._write_reflection_evidence(
                    record,
                    prior=prior,
                    include_repository_reference=True,
                )
                items.append(
                    {
                        "instance_id": record["instance_id"],
                        "validation_playbook": internal.serialize(),
                        "repository": dict(repository),
                        "source_access_issue": str(record["issue"]),
                        "image_authority": _image_authority_for_repository(
                            str(record["instance_id"]),
                            repository,
                            self.image_records,
                        ),
                        "evidence_dir": str(evidence_dir),
                        "prompt_values": {
                            "internal_playbook": internal.serialize(),
                            "evidence_path": "/evidence",
                        },
                    }
                )
            priors = [
                item["agent_output"]
                for item in self.executor.run_wave("repo_reflector", items)
            ]
        return priors


class HPCPairedRepoPlaybookProposalAgents(HPCPlaybookProposalAgents):
    """One repository-aware Reflector over both sides of each Plan pair."""

    def __init__(
        self,
        executor: PlaybookHPCExecutor,
        *,
        image_records: Mapping[str, Any],
        maximum_tokens: int,
        maximum_bullet_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
        require_concern_coverage: bool = False,
        evidence_contract: str = "legacy_v1",
        require_curator_self_check: bool = False,
    ) -> None:
        super().__init__(
            executor,
            maximum_tokens=maximum_tokens,
            maximum_bullet_tokens=maximum_bullet_tokens,
            token_counter=token_counter,
            visible_renderer=render_concern_playbook,
            require_concern_coverage=require_concern_coverage,
            evidence_contract=evidence_contract,
            require_curator_self_check=require_curator_self_check,
        )
        self.image_records = image_records

    def _write_pair_evidence(
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
        root = self.executor.run_dir / "pair_reflection_evidence" / identity
        atomic_json(
            root / "pair.json",
            {
                key: record.get(key)
                for key in (
                    "instance_id",
                    "task_id",
                    "issue",
                    "repository",
                    "score",
                    "pair_output",
                    "checker_visible_playbook",
                )
            },
        )
        for side_name in ("resolved_side", "unresolved_side"):
            side = record.get(side_name)
            if not isinstance(side, dict):
                raise ValueError(f"paired Reflection record lacks {side_name}")
            historical = self._materialize_historical_evidence(
                dict(side.get("historical_evidence") or {})
            )
            side_root = root / side_name
            atomic_json(
                side_root / "review_input.json",
                {
                    key: side.get(key)
                    for key in (
                        "observation_id",
                        "plan",
                        "plan_sha256",
                        "checker_output",
                    )
                },
            )
            atomic_json(
                side_root / "plan_trajectory.json",
                historical.get("plan_trajectory", []),
            )
            atomic_json(
                side_root / "code_trajectory.json",
                historical.get("code_trajectory", []),
            )
            atomic_json(
                side_root / "evaluator_result.json",
                historical.get("evaluator_result", {}),
            )
            (side_root / "generated.patch").write_text(
                str(historical.get("generated_patch", "")), encoding="utf-8"
            )
        if prior is not None:
            atomic_json(root / "prior_reflection.json", dict(prior))
        atomic_json(
            root / "manifest.json",
            {
                "schema_version": 1,
                "instance_id": record["instance_id"],
                "task_id": record["task_id"],
                "files": [
                    "pair.json",
                    "resolved_side/review_input.json",
                    "resolved_side/plan_trajectory.json",
                    "resolved_side/code_trajectory.json",
                    "resolved_side/evaluator_result.json",
                    "resolved_side/generated.patch",
                    "unresolved_side/review_input.json",
                    "unresolved_side/plan_trajectory.json",
                    "unresolved_side/code_trajectory.json",
                    "unresolved_side/evaluator_result.json",
                    "unresolved_side/generated.patch",
                    *(["prior_reflection.json"] if prior is not None else []),
                ],
                "contains_repository_reference": True,
            },
        )
        return root

    def reflect_batch(self, records: Sequence[Mapping[str, Any]], rounds: int):
        priors: list[Mapping[str, Any] | None] = [None] * len(records)
        for _ in range(rounds):
            items = []
            for record, prior in zip(records, priors, strict=True):
                internal = RejectPlaybook.parse(record["internal_playbook"])
                repository = record.get("repository")
                task_id = str(record.get("task_id", ""))
                if not isinstance(repository, dict) or not task_id:
                    raise ValueError("paired Reflection lacks repository identity")
                evidence_dir = self._write_pair_evidence(record, prior=prior)
                items.append(
                    {
                        "instance_id": record["instance_id"],
                        "validation_playbook": internal.serialize(),
                        "repository": dict(repository),
                        "source_access_issue": str(record["issue"]),
                        "image_authority": _image_authority_for_repository(
                            task_id, repository, self.image_records
                        ),
                        "evidence_dir": str(evidence_dir),
                        "prompt_values": {
                            "internal_playbook": internal.serialize(),
                            "evidence_path": "/evidence",
                        },
                    }
                )
            priors = [
                item["agent_output"]
                for item in self.executor.run_wave("paired_repo_reflector", items)
            ]
        return priors
