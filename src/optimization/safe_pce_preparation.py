"""Prepare explicit task splits for the existing single-dataset HPC stager.

Small frozen source inputs are bundled with the learning snapshot. SIFs,
prepared history and dependency-cache payloads remain shared remote artifacts.
No Agent or scheduler is invoked by preparation.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import yaml

from src.optimization.safe_pce_execution import (
    load_pce_config, pce_cases, resolved_pce_payload, sha256, validate_learning_source,
)
from src.optimization.safe_pce_learning import TASK_SEMANTICS

ROOT = Path(__file__).resolve().parents[2]


def _path(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _write_once(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"prepared input already exists with different bytes: {path}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def prepare(*, learning_config: Path, pce_config: Path, selection: dict,
            snapshot: Path, run_dir: Path, output_config: Path,
            max_iterations: int | None = None, minibatch_size: int | None = None,
            max_metric_calls: int | None = None):
    """User supplies task IDs; this function neither samples nor selects by R/U."""
    if set(selection) != {"train", "validation"}:
        raise ValueError("split selection must contain exactly train and validation ID arrays")
    if any(not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item for item in ids)
           for ids in selection.values()):
        raise ValueError("both splits require nonempty task ID arrays")
    identifiers = selection["train"] + selection["validation"]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("task IDs must be unique across train/validation")
    mode, base = load_pce_config(pce_config)
    validate_learning_source(base)
    available = {case.instance_id for case in pce_cases(mode, base)}
    if set(identifiers) - available:
        raise ValueError("split selection includes unavailable or unselected PCE tasks")
    raw = deepcopy(yaml.safe_load(learning_config.read_text()))
    if not raw.get("hybrid", {}).get("enabled"):
        raise ValueError("learning baseline must use the established hybrid ACE flow")
    for name, value in (("max_iterations", max_iterations),
                        ("reflection_minibatch_size", minibatch_size),
                        ("max_metric_calls", max_metric_calls)):
        if value is not None:
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
            raw["search"][name] = value
    if max_iterations is not None:
        raw["search"]["min_proposals"] = max_iterations
    # A baseline's projected cost is not the projection for a new split/budget.
    search = raw["search"]
    search["projection_metric_calls"] = len(selection["validation"]) + search["max_iterations"] * (
        2 * search["reflection_minibatch_size"] * search.get("train_case_repetitions", 1)
        + len(selection["validation"]))
    if len(selection["train"]) < int(raw["search"]["reflection_minibatch_size"]):
        raise ValueError("minibatch exceeds distinct training tasks")
    source_manifest = base.dataset_snapshot / "manifest.json"
    manifest = json.loads(source_manifest.read_text())
    instances = Path(manifest.get("instances_file", "instances.jsonl"))
    if instances.is_absolute() or ".." in instances.parts:
        raise ValueError("PCE instances_file must be a safe source-relative path")
    source_dir = snapshot / "pce_source"
    for source, target in ((source_manifest, source_dir / "manifest.json"),
                           (base.dataset_snapshot / instances, source_dir / instances),
                           (base.image_manifest, source_dir / "images.json")):
        _write_once(target, source.read_bytes())
    bundled = resolved_pce_payload(base)
    planner_prompt = ROOT / "configs/prompts/safe_pce_rules_planner_v1.yaml"
    bundled["prompts"]["plan_system"] = yaml.safe_load(planner_prompt.read_text())["prompts"]["plan_system"]
    # The same source bytes keep existing source/row/image hash verification.
    bundled["paths"]["dataset_snapshot"] = _path(source_dir)
    bundled["paths"]["image_manifest"] = _path(source_dir / "images.json")
    for key in ("selection_manifest", "dependency_cache_manifest"):
        if bundled["paths"].get(key):
            source = Path(bundled["paths"][key])
            target = source_dir / f"{key}{source.suffix}"
            _write_once(target, source.read_bytes())
            bundled["paths"][key] = _path(target)
    if mode == "swe_verified_pce":
        bundled.setdefault("execution", {}).pop("manifest", None)
        bundled["execution"]["pce_runs_per_instance"] = 1
    else:
        bundled["pce"] = {"repetitions": 1}
    bundled["paths"]["run_dir"] = _path(run_dir / "pce_evaluations")
    bundled_path = source_dir / "base_pce.yaml"
    bundled.setdefault("hpc", {})["worker_config_path"] = _path(bundled_path)
    _write_once(bundled_path, yaml.safe_dump(bundled, sort_keys=False).encode())
    split_manifest = {"schema_version": 1, "task_semantics": TASK_SEMANTICS,
                      "complete": True, "provisional": False, "pce_mode": mode,
                      "source_manifest_sha256": sha256(source_manifest),
                      "image_manifest_sha256": sha256(base.image_manifest),
                      "source_pce_config_sha256": sha256(pce_config),
                      "engineering_baseline_config_sha256": sha256(learning_config)}
    for split, ids in selection.items():
        path = snapshot / f"{split}.jsonl"
        _write_once(path, "".join(json.dumps({"instance_id": identifier, "split": split}) + "\n" for identifier in ids).encode())
        split_manifest[f"{split}_instances"] = len(ids)
        split_manifest[f"{split}_sha256"] = sha256(path)
    _write_once(snapshot / "manifest.json", (json.dumps(split_manifest, sort_keys=True, indent=2) + "\n").encode())
    # Preserve learning Agent models, hard limits and transport, changing only
    # the task/backend and explicit new run identity. Never adopt old outputs.
    raw["mode"] = "safe_pce_hybrid_ace"
    raw["task"] = {"semantics": TASK_SEMANTICS}
    raw.pop("checker", None)
    raw.pop("behavioral_repository", None)
    raw.pop("prompts", None)
    raw["paths"] = {"dataset_snapshot": _path(snapshot), "safe_pce_config": _path(bundled_path),
                    "initial_guideline": "configs/prompts/safe_pce_hybrid_seed_v1.json", "run_dir": _path(run_dir)}
    raw["search"]["primary_metric"] = "resolved_rate"
    raw["hybrid"]["prompt_bundle"] = "configs/prompts/safe_pce_hybrid_ace_v1.yaml"
    raw["hybrid"]["shared_guide_enabled"] = False
    raw["hybrid"].pop("shared_guide_words", None)
    raw["hybrid"].pop("shared_guide_tokens", None)
    raw["hybrid"].pop("checker_model", None)
    raw["hybrid"].pop("repository_history_cache_dir", None)
    raw["hybrid"]["dataset_file_sha256"] = {name: sha256(snapshot / name) for name in ("manifest.json", "train.jsonl", "validation.jsonl")}
    old_identity = raw.get("experiment_contract", {}).get("identity", learning_config.stem)
    identity = output_config.stem
    for key in ("remote_project_dir", "remote_task_dir"):
        if key in raw.get("hpc", {}):
            raw["hpc"][key] = raw["hpc"][key].replace(old_identity, identity)
    raw.setdefault("hpc", {}).update(job_name_prefix=identity, worker_config_path=_path(output_config))
    raw["experiment_contract"] = {"identity": identity, "launch_authorized": False,
                                  "engineering_baseline_config": _path(learning_config),
                                  "safe_pce_source_config": _path(pce_config),
                                  "objective": "resolved_rate", "planner_records_application": True}
    _write_once(output_config, yaml.safe_dump(raw, sort_keys=False).encode())
    return output_config
