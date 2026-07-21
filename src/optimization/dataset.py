"""Load the immutable Verified Round 1 snapshot with leakage checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from src.optimization.models import GEPACase, RepositoryRef

CHECKER_INPUT_KEYS = {"issue_description", "plan", "repository"}
ASI_KEYS = {
    "plan_trajectory",
    "code_trajectory",
    "generated_patch",
    "evaluator_result",
}


class GEPACaseLoader:
    """In-memory GEPA loader keyed by stable benchmark instance IDs.

    GEPA shares one evaluation cache between its train and validation loaders.
    Using list offsets as IDs therefore aliases train item 0 with validation
    item 0.  Stable, split-disjoint instance IDs keep those cache entries
    distinct and make persisted GEPA state auditable without a positional join.
    """

    def __init__(self, cases: Sequence[GEPACase]) -> None:
        self._cases_by_id = {case.instance_id: case for case in cases}
        if len(self._cases_by_id) != len(cases):
            raise ValueError("GEPA case instance IDs must be unique")
        self._ids = [case.instance_id for case in cases]

    def all_ids(self) -> list[str]:
        return list(self._ids)

    def fetch(self, ids: Sequence[str]) -> list[GEPACase]:
        return [self._cases_by_id[instance_id] for instance_id in ids]

    def __len__(self) -> int:
        return len(self._ids)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_cases(path: str | Path) -> list[GEPACase]:
    cases = []
    for raw in _read_jsonl(Path(path)):
        checker_input = raw.get("checker_input")
        asi = raw.get("asi")
        if not isinstance(checker_input, dict) or set(checker_input) != CHECKER_INPUT_KEYS:
            raise ValueError(
                f"{raw.get('instance_id')}: invalid checker_input boundary"
            )
        if not isinstance(asi, dict) or set(asi) != ASI_KEYS:
            raise ValueError(f"{raw.get('instance_id')}: invalid ASI boundary")
        repository = checker_input["repository"]
        if not isinstance(repository, dict):
            raise ValueError("checker_input.repository must be a mapping")
        resolved = raw.get("resolved")
        if not isinstance(resolved, bool):
            raise ValueError(f"{raw.get('instance_id')}: resolved must be boolean")
        cases.append(
            GEPACase(
                instance_id=str(raw["instance_id"]),
                split=str(raw["split"]),
                resolved=resolved,
                issue_description=str(checker_input["issue_description"]),
                plan=str(checker_input["plan"]),
                repository=RepositoryRef(
                    repo=str(repository["repo"]),
                    base_commit=str(repository["base_commit"]),
                    instance_id=str(repository["instance_id"]),
                ),
                asi=asi,
            )
        )
    return cases


def load_snapshot(snapshot_dir: str | Path) -> tuple[list[GEPACase], list[GEPACase]]:
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("GEPA requires a complete, non-provisional snapshot")
    train = load_cases(root / "train.jsonl")
    validation = load_cases(root / "validation.jsonl")
    if {case.instance_id for case in train} & {
        case.instance_id for case in validation
    }:
        raise ValueError("train and validation instance IDs overlap")
    if len(train) != manifest.get("train_instances"):
        raise ValueError("train count does not match manifest")
    if len(validation) != manifest.get("validation_instances"):
        raise ValueError("validation count does not match manifest")
    return train, validation
