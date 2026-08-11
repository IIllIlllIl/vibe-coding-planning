"""Configuration for additive Offline Checker-only evaluations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from typing import Any

import yaml

from src.optimization.config import OptimizationConfig, load_optimization_config


@dataclass(frozen=True)
class CheckOnlyDatasetConfig:
    name: str
    type: str
    language: str
    snapshot: Path
    case_file: str
    cleaned_file: str
    exclusions_file: str


@dataclass(frozen=True)
class CheckOnlyConfig:
    config_path: Path
    runtime_config_path: Path
    runtime: OptimizationConfig
    dataset: CheckOnlyDatasetConfig
    guideline_bundle: Path
    guideline_labels: tuple[str, ...]
    run_dir: Path


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _resolve(value: str, *, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_check_only_config(
    path: str | Path,
    *,
    require_api_keys: bool = True,
) -> CheckOnlyConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "offline_check_only":
        raise ValueError("check-only config requires mode: offline_check_only")
    root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
    paths = _mapping(raw.get("paths"), "paths")
    dataset_raw = _mapping(raw.get("dataset"), "dataset")
    check_only = _mapping(raw.get("check_only"), "check_only")

    runtime_config_path = _resolve(str(paths["checker_runtime_config"]), root=root)
    run_dir = _resolve(str(paths["run_dir"]), root=root)
    runtime = replace(
        load_optimization_config(
            runtime_config_path,
            require_api_keys=require_api_keys,
        ),
        run_dir=run_dir,
    )
    labels_raw = check_only.get("guidelines")
    if not isinstance(labels_raw, list) or not labels_raw:
        raise ValueError("check_only.guidelines must be a non-empty list")
    labels = tuple(str(label) for label in labels_raw)
    if len(set(labels)) != len(labels):
        raise ValueError("check_only.guidelines must be unique")

    dataset_type = str(dataset_raw["type"])
    if dataset_type not in {"swebench", "polybench"}:
        raise ValueError("dataset.type must be 'swebench' or 'polybench'")
    dataset = CheckOnlyDatasetConfig(
        name=str(dataset_raw["name"]),
        type=dataset_type,
        language=str(dataset_raw.get("language", "")),
        snapshot=_resolve(str(paths["dataset_snapshot"]), root=root),
        case_file=str(dataset_raw.get("case_file", "raw_validation.jsonl")),
        cleaned_file=str(dataset_raw.get("cleaned_file", "validation.jsonl")),
        exclusions_file=str(dataset_raw.get("exclusions_file", "exclusions.json")),
    )
    for filename in (
        dataset.case_file,
        dataset.cleaned_file,
        dataset.exclusions_file,
    ):
        if Path(filename).name != filename:
            raise ValueError("dataset file names must not contain directories")

    return CheckOnlyConfig(
        config_path=config_path,
        runtime_config_path=runtime_config_path,
        runtime=runtime,
        dataset=dataset,
        guideline_bundle=_resolve(str(paths["guideline_bundle"]), root=root),
        guideline_labels=labels,
        run_dir=run_dir,
    )
