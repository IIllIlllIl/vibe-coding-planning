"""Load immutable within-task Plan pairs without cross-split task leakage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from src.optimization.models import (
    PairedGEPACase,
    PairedPlanObservation,
    RepositoryRef,
)


class PairedGEPACaseLoader:
    def __init__(self, cases: Sequence[PairedGEPACase]) -> None:
        self._cases_by_id = {case.instance_id: case for case in cases}
        if len(self._cases_by_id) != len(cases):
            raise ValueError("paired GEPA instance IDs must be unique")
        self._ids = [case.instance_id for case in cases]

    def all_ids(self) -> list[str]:
        return list(self._ids)

    def fetch(self, ids: Sequence[str]) -> list[PairedGEPACase]:
        return [self._cases_by_id[item] for item in ids]

    def __len__(self) -> int:
        return len(self._ids)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _observation(raw: Any, *, pair_id: str, side: str) -> PairedPlanObservation:
    if not isinstance(raw, dict) or set(raw) != {
        "observation_id",
        "plan",
        "plan_sha256",
        "historical_evidence",
    }:
        raise ValueError(f"{pair_id}: invalid {side} observation boundary")
    plan = raw["plan"]
    if not isinstance(plan, str) or not plan.strip():
        raise ValueError(f"{pair_id}: {side} Plan is empty")
    observed = hashlib.sha256(plan.encode("utf-8")).hexdigest()
    if raw["plan_sha256"] != observed:
        raise ValueError(f"{pair_id}: {side} Plan hash mismatch")
    evidence = raw["historical_evidence"]
    if not isinstance(evidence, dict):
        raise ValueError(f"{pair_id}: {side} evidence must be a mapping")
    return PairedPlanObservation(
        observation_id=str(raw["observation_id"]),
        plan=plan,
        plan_sha256=observed,
        historical_evidence=dict(evidence),
    )


def load_paired_cases(path: str | Path) -> list[PairedGEPACase]:
    cases: list[PairedGEPACase] = []
    for raw in _read_jsonl(Path(path)):
        expected = {
            "schema_version",
            "pair_id",
            "task_id",
            "split",
            "issue_description",
            "repository",
            "resolved_observation",
            "unresolved_observation",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("paired dataset row has an invalid boundary")
        pair_id = str(raw["pair_id"])
        task_id = str(raw["task_id"])
        repository = raw["repository"]
        if not isinstance(repository, dict) or set(repository) != {
            "repo",
            "base_commit",
            "instance_id",
        }:
            raise ValueError(f"{pair_id}: invalid repository identity")
        if repository["instance_id"] != task_id:
            raise ValueError(f"{pair_id}: task/repository identity mismatch")
        resolved = _observation(
            raw["resolved_observation"], pair_id=pair_id, side="resolved"
        )
        unresolved = _observation(
            raw["unresolved_observation"], pair_id=pair_id, side="unresolved"
        )
        if resolved.observation_id == unresolved.observation_id:
            raise ValueError(f"{pair_id}: pair sides must be distinct observations")
        if resolved.plan_sha256 == unresolved.plan_sha256:
            raise ValueError(f"{pair_id}: identical Plans cannot form a contrast")
        cases.append(
            PairedGEPACase(
                instance_id=pair_id,
                task_id=task_id,
                split=str(raw["split"]),
                issue_description=str(raw["issue_description"]),
                repository=RepositoryRef(
                    repo=str(repository["repo"]),
                    base_commit=str(repository["base_commit"]),
                    instance_id=task_id,
                ),
                resolved_observation=resolved,
                unresolved_observation=unresolved,
            )
        )
    return cases


def load_paired_snapshot(
    snapshot_dir: str | Path,
) -> tuple[list[PairedGEPACase], list[PairedGEPACase]]:
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("paired GEPA requires a complete, non-provisional snapshot")
    if manifest.get("data_unit") != "within_task_plan_pair":
        raise ValueError("snapshot is not a within-task Plan-pair dataset")
    train = load_paired_cases(root / "train.jsonl")
    validation = load_paired_cases(root / "validation.jsonl")
    train_pairs = {case.instance_id for case in train}
    validation_pairs = {case.instance_id for case in validation}
    if train_pairs & validation_pairs:
        raise ValueError("train and validation pair IDs overlap")
    train_tasks = {case.task_id for case in train}
    validation_tasks = {case.task_id for case in validation}
    if train_tasks & validation_tasks:
        raise ValueError("train and validation task IDs overlap")
    if len(train) != manifest.get("train_pairs"):
        raise ValueError("train pair count does not match manifest")
    if len(validation) != manifest.get("validation_pairs"):
        raise ValueError("validation pair count does not match manifest")
    return train, validation
