"""Strict loader for immutable Behavioral Plan Acceptability snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from src.optimization.behavioral_models import (
    DECISIONS,
    TASK_SEMANTICS,
    BehavioralGEPACase,
    BehavioralRepositoryProxy,
)

TOP_LEVEL_KEYS = {
    "instance_id",
    "split",
    "task_semantics",
    "checker_input",
    "supervision",
    "reflection_evidence",
    "audit_provenance",
}
CHECKER_INPUT_KEYS = {"pre_p1_context", "proposed_plan_p1", "repository_proxy"}
REPOSITORY_PROXY_KEYS = {
    "repo",
    "proxy_commit",
    "instance_id",
    "state_semantics",
    "conflict_authority",
}
SUPERVISION_KEYS = {"decision", "confidence", "signal"}


class BehavioralCaseLoader:
    def __init__(self, cases: Sequence[BehavioralGEPACase]) -> None:
        self._cases_by_id = {case.instance_id: case for case in cases}
        if len(self._cases_by_id) != len(cases):
            raise ValueError("Behavioral case instance IDs must be unique")
        self._ids = [case.instance_id for case in cases]

    def all_ids(self) -> list[str]:
        return list(self._ids)

    def fetch(self, ids: Sequence[str]) -> list[BehavioralGEPACase]:
        return [self._cases_by_id[instance_id] for instance_id in ids]

    def __len__(self) -> int:
        return len(self._ids)


def _mapping(value: Any, *, instance_id: str, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{instance_id}: {field} must be a mapping")
    return value


def _exact_keys(
    value: dict[str, Any], expected: set[str], *, instance_id: str, field: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{instance_id}: invalid {field} boundary")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_behavioral_cases(path: str | Path) -> list[BehavioralGEPACase]:
    cases: list[BehavioralGEPACase] = []
    for raw in _read_jsonl(Path(path)):
        instance_id = str(raw.get("instance_id", "<missing>"))
        _exact_keys(raw, TOP_LEVEL_KEYS, instance_id=instance_id, field="case")
        if raw["task_semantics"] != TASK_SEMANTICS:
            raise ValueError(f"{instance_id}: unsupported task_semantics")

        checker = _mapping(
            raw["checker_input"], instance_id=instance_id, field="checker_input"
        )
        _exact_keys(
            checker,
            CHECKER_INPUT_KEYS,
            instance_id=instance_id,
            field="checker_input",
        )
        repository = _mapping(
            checker["repository_proxy"],
            instance_id=instance_id,
            field="checker_input.repository_proxy",
        )
        _exact_keys(
            repository,
            REPOSITORY_PROXY_KEYS,
            instance_id=instance_id,
            field="checker_input.repository_proxy",
        )
        supervision = _mapping(
            raw["supervision"], instance_id=instance_id, field="supervision"
        )
        _exact_keys(
            supervision,
            SUPERVISION_KEYS,
            instance_id=instance_id,
            field="supervision",
        )
        if supervision["decision"] not in DECISIONS:
            raise ValueError(f"{instance_id}: invalid behavioral decision")
        if supervision["confidence"] != "high":
            raise ValueError(f"{instance_id}: only high-confidence cases are eligible")
        context = checker["pre_p1_context"]
        if not isinstance(context, list) or not all(
            isinstance(event, dict) for event in context
        ):
            raise ValueError(f"{instance_id}: pre_p1_context must be an event list")
        proposed_plan = checker["proposed_plan_p1"]
        if not isinstance(proposed_plan, str) or not proposed_plan.strip():
            raise ValueError(f"{instance_id}: proposed_plan_p1 must be non-empty")
        reflection = _mapping(
            raw["reflection_evidence"],
            instance_id=instance_id,
            field="reflection_evidence",
        )
        audit = _mapping(
            raw["audit_provenance"],
            instance_id=instance_id,
            field="audit_provenance",
        )
        mirror_relpath = audit.get("mirror_relpath")
        if not isinstance(mirror_relpath, str) or not mirror_relpath:
            raise ValueError(
                f"{instance_id}: audit_provenance.mirror_relpath is required"
            )
        relative_parts = Path(mirror_relpath).parts
        if Path(mirror_relpath).is_absolute() or ".." in relative_parts:
            raise ValueError(
                f"{instance_id}: audit_provenance.mirror_relpath must be relative"
            )

        cases.append(
            BehavioralGEPACase(
                instance_id=instance_id,
                split=str(raw["split"]),
                decision=str(supervision["decision"]),
                confidence=str(supervision["confidence"]),
                signal=str(supervision["signal"]),
                pre_p1_context=tuple(context),
                proposed_plan_p1=proposed_plan,
                repository=BehavioralRepositoryProxy(
                    repo=str(repository["repo"]),
                    proxy_commit=str(repository["proxy_commit"]),
                    instance_id=str(repository["instance_id"]),
                    state_semantics=str(repository["state_semantics"]),
                    conflict_authority=str(repository["conflict_authority"]),
                ),
                reflection_evidence=reflection,
                audit_provenance=audit,
            )
        )
    return cases


def load_behavioral_snapshot(
    snapshot_dir: str | Path,
) -> tuple[list[BehavioralGEPACase], list[BehavioralGEPACase]]:
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError(
            "Behavioral GEPA requires a complete, non-provisional snapshot"
        )
    if manifest.get("task_semantics") != TASK_SEMANTICS:
        raise ValueError("snapshot task_semantics is not Behavioral v1")
    train = load_behavioral_cases(root / "train.jsonl")
    validation = load_behavioral_cases(root / "validation.jsonl")
    train_ids = {case.instance_id for case in train}
    validation_ids = {case.instance_id for case in validation}
    if train_ids & validation_ids:
        raise ValueError("train and validation instance IDs overlap")
    if any(case.split != "train" for case in train):
        raise ValueError("train.jsonl contains a non-train split")
    if any(case.split != "validation" for case in validation):
        raise ValueError("validation.jsonl contains a non-validation split")
    if len(train) != manifest.get("train_instances"):
        raise ValueError("train count does not match manifest")
    if len(validation) != manifest.get("validation_instances"):
        raise ValueError("validation count does not match manifest")
    return train, validation
