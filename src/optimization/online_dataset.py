"""Load online GEPA cases without offline labels or ASI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.optimization.models import RepositoryRef
from src.optimization.online_models import OnlineGEPACase

ONLINE_INPUT_KEYS = {"issue_description", "repository"}
OFFLINE_CHECKER_INPUT_KEYS = {"issue_description", "plan", "repository"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _online_case_from_record(raw: dict[str, Any]) -> OnlineGEPACase:
    """Build an online case while dropping offline-only fields.

    The current immutable offline GEPA snapshot stores issue/repository under
    ``checker_input`` next to a historical plan. Online GEPA may reuse that
    snapshot for instance identity and split only, but the historical plan,
    resolved label, and ASI must not be represented in the returned object.
    """

    payload = raw.get("online_input")
    if payload is None:
        payload = raw.get("checker_input")
    if not isinstance(payload, dict):
        raise ValueError(f"{raw.get('instance_id')}: missing online input")

    keys = set(payload)
    if keys == OFFLINE_CHECKER_INPUT_KEYS:
        payload = {
            "issue_description": payload["issue_description"],
            "repository": payload["repository"],
        }
    elif keys != ONLINE_INPUT_KEYS:
        raise ValueError(f"{raw.get('instance_id')}: invalid online input boundary")

    repository = payload["repository"]
    if not isinstance(repository, dict):
        raise ValueError("online_input.repository must be a mapping")

    return OnlineGEPACase(
        instance_id=str(raw["instance_id"]),
        split=str(raw["split"]),
        issue_description=str(payload["issue_description"]),
        repository=RepositoryRef(
            repo=str(repository["repo"]),
            base_commit=str(repository["base_commit"]),
            instance_id=str(repository["instance_id"]),
        ),
    )


def load_online_cases(path: str | Path) -> list[OnlineGEPACase]:
    return [_online_case_from_record(raw) for raw in _read_jsonl(Path(path))]


def load_online_snapshot(
    snapshot_dir: str | Path,
) -> tuple[list[OnlineGEPACase], list[OnlineGEPACase]]:
    root = Path(snapshot_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("online GEPA requires a complete, non-provisional snapshot")
    train = load_online_cases(root / "train.jsonl")
    validation = load_online_cases(root / "validation.jsonl")
    if {case.instance_id for case in train} & {
        case.instance_id for case in validation
    }:
        raise ValueError("train and validation instance IDs overlap")
    if len(train) != manifest.get("train_instances"):
        raise ValueError("train count does not match manifest")
    if len(validation) != manifest.get("validation_instances"):
        raise ValueError("validation count does not match manifest")
    return train, validation
