"""Validation-only inputs that never manufacture GEPA train or ASI fields."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from src.offline_check_only.config import CheckOnlyDatasetConfig, file_sha256


@dataclass(frozen=True)
class CheckOnlyCase:
    instance_id: str
    split: str
    resolved: bool
    issue_description: str
    plan: str
    repository: dict[str, str]
    task_category: str
    language: str
    excluded_from_cleaned: bool = False
    exclusion_reason: str | None = None
    asi: dict[str, Any] = field(default_factory=dict)

    def checker_payload(self) -> dict[str, Any]:
        return {
            "issue_description": self.issue_description,
            "plan": self.plan,
            "repository": dict(self.repository),
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_validation_cases(
    dataset: CheckOnlyDatasetConfig,
) -> tuple[list[CheckOnlyCase], dict[str, Any]]:
    root = dataset.snapshot
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or manifest.get("provisional"):
        raise ValueError("check-only requires a complete, non-provisional snapshot")
    if manifest.get("dataset") != dataset.name:
        raise ValueError("configured dataset.name differs from snapshot manifest")
    if manifest.get("dataset_type") != dataset.type:
        raise ValueError("configured dataset.type differs from snapshot manifest")
    if dataset.language and manifest.get("language") != dataset.language:
        raise ValueError("configured dataset.language differs from snapshot manifest")

    case_path = root / dataset.case_file
    expected_hash = manifest.get("raw_validation_sha256")
    if dataset.case_file == "validation.jsonl":
        expected_hash = manifest.get("validation_sha256")
    if expected_hash and file_sha256(case_path) != expected_hash:
        raise ValueError("validation case file hash differs from snapshot manifest")

    exclusions_path = root / dataset.exclusions_file
    if (
        manifest.get("exclusions_sha256")
        and file_sha256(exclusions_path) != manifest["exclusions_sha256"]
    ):
        raise ValueError("exclusions hash differs from snapshot manifest")
    exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))
    excluded = {
        str(item["instance_id"]): str(item["reason_code"])
        for item in exclusions
    }
    cases: list[CheckOnlyCase] = []
    for raw in _read_jsonl(case_path):
        checker_input = raw.get("checker_input")
        if not isinstance(checker_input, dict) or set(checker_input) != {
            "issue_description",
            "plan",
            "repository",
        }:
            raise ValueError(f"{raw.get('instance_id')}: invalid Checker boundary")
        repository = checker_input.get("repository")
        if not isinstance(repository, dict):
            raise ValueError("checker_input.repository must be a mapping")
        required = {"repo", "base_commit", "instance_id", "dataset_type", "image_name"}
        if not required <= set(repository):
            raise ValueError(f"{raw.get('instance_id')}: incomplete repository metadata")
        if repository["dataset_type"] != dataset.type:
            raise ValueError(f"{raw.get('instance_id')}: dataset type mismatch")
        resolved = raw.get("resolved")
        if not isinstance(resolved, bool):
            raise ValueError(f"{raw.get('instance_id')}: resolved must be boolean")
        instance_id = str(raw["instance_id"])
        cases.append(
            CheckOnlyCase(
                instance_id=instance_id,
                split=str(raw.get("split", "validation")),
                resolved=resolved,
                issue_description=str(checker_input["issue_description"]),
                plan=str(checker_input["plan"]),
                repository={key: str(value) for key, value in repository.items()},
                task_category=str(raw.get("task_category", "")),
                language=str(raw.get("language", "")),
                excluded_from_cleaned=instance_id in excluded,
                exclusion_reason=excluded.get(instance_id),
            )
        )
    if len({case.instance_id for case in cases}) != len(cases):
        raise ValueError("validation instance IDs must be unique")
    expected_count = (manifest.get("raw") or {}).get("instances")
    if dataset.case_file == "validation.jsonl":
        expected_count = (manifest.get("cleaned") or {}).get("instances")
    if expected_count is not None and len(cases) != int(expected_count):
        raise ValueError("validation case count differs from snapshot manifest")
    if dataset.case_file != dataset.cleaned_file:
        cleaned_path = root / dataset.cleaned_file
        if (
            manifest.get("validation_sha256")
            and file_sha256(cleaned_path) != manifest["validation_sha256"]
        ):
            raise ValueError("cleaned validation hash differs from snapshot manifest")
        cleaned_ids = {
            str(item["instance_id"])
            for item in _read_jsonl(cleaned_path)
        }
        raw_ids = {case.instance_id for case in cases}
        if cleaned_ids != raw_ids - set(excluded):
            raise ValueError("cleaned validation IDs do not match exclusions")
    return cases, manifest
