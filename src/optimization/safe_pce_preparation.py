"""Prepare explicit task splits for the existing single-dataset HPC stager.

Small frozen source inputs are bundled with the learning snapshot. SIFs,
prepared history and dependency-cache payloads remain shared remote artifacts.
No Agent or scheduler is invoked by preparation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import yaml

from src.artifact_policy import mark_private_artifact_dir

from src.optimization.safe_pce_execution import (
    load_pce_config, pce_cases, resolved_pce_payload, sha256, validate_learning_source,
)

ROOT = Path(__file__).resolve().parents[2]


def prepare_historical_replay(*, learning_config, proposal_path, evaluations_path,
                              snapshot, run_dir, output_config):
    """Freeze one historical training draw for a fresh subagent learning pilot.

    Original current Ref/Cur and candidate outputs are deliberately excluded.
    Prior training reports seed the same ACE ledger; validation stays evaluator-only.
    """
    from src.optimization.behavioral_hybrid_ace import _playbook
    from src.optimization.playbook_adapter import GlobalPlaybookCounters
    from src.optimization.safe_pce_learning import _iter_records
    raw = deepcopy(yaml.safe_load(Path(learning_config).read_text()))
    source = Path(raw["paths"]["dataset_snapshot"])
    if not source.is_absolute():
        source = ROOT / source
    proposal = json.loads(Path(proposal_path).read_text())
    records = proposal["records"]
    ids = [record["instance_id"] for record in records]
    if len(ids) != len(set(ids)) or any(record.get("split") != "train" for record in records):
        raise ValueError("replay requires distinct original training records")
    seed = Path(raw["paths"]["initial_guideline"])
    if not seed.is_absolute():
        seed = ROOT / seed
    parent_rules = seed.read_text().strip()
    if json.loads(parent_rules) != proposal["parent"]:
        raise ValueError("this one-proposal pilot requires the original seed parent")
    digest = hashlib.sha256(parent_rules.encode()).hexdigest()
    validation_ids = {json.loads(line)["instance_id"] for line in (source / "validation.jsonl").open() if line.strip()}
    train_rows, validation_rows = {}, {}
    by_id = {record["instance_id"]: record for record in records}
    for row in _iter_records(Path(evaluations_path)):
        if row["candidate_sha256"] != digest:
            continue
        if row["split"] == "validation" and row["instance_id"] in validation_ids:
            validation_rows[row["instance_id"]] = row
        elif row["split"] == "train" and row["instance_id"] in by_id:
            if row["output"]["pce_runs"] == by_id[row["instance_id"]]["pce_runs"]:
                train_rows[row["instance_id"]] = row
    if set(train_rows) != set(ids) or set(validation_rows) != validation_ids:
        raise ValueError("original parent training/seed-validation observations are incomplete")
    history = deepcopy(proposal["history"])
    history["events"] = {key: event for key, event in history["events"].items() if event["instance_id"] not in set(ids)}
    reports = {event["reflection_sha256"] for event in history["events"].values()}
    contexts = {event["checker_context_sha256"] for event in history["events"].values()}
    history["reports"] = {key: history["reports"][key] for key in reports}
    history["contexts"] = {key: history["contexts"][key] for key in contexts}
    ledger = GlobalPlaybookCounters()
    book = ledger.hydrate(_playbook(proposal["parent"]))
    identities = {bullet.text.casefold() for bullet in book.bullets}
    if any(item["text"].casefold() not in identities for context in history["contexts"].values() for item in context):
        raise ValueError("pilot prior history contains another artifact; use an explicitly frozen ledger")
    counted, pending = ledger.apply_reviews(book, list(history["reports"].values()))
    ledger.commit(counted, pending, feedback=history)
    replay = {"schema_version": 1, "parent": {"rules": parent_rules}, "records": records,
        "trace": {"i": 0, "selected_program_candidate": 0, "subsample_ids": ids,
                  "subsample_scores": [train_rows[identifier]["score"] for identifier in ids]},
        "parent_rows": [train_rows[identifier] for identifier in ids],
        "validation": [validation_rows[identifier] for identifier in sorted(validation_ids)],
        "ledger": ledger.export(), "provenance": {
            "source_proposal_sha256": sha256(Path(proposal_path)),
            "source_evaluations_sha256": sha256(Path(evaluations_path)),
            "source_config_sha256": sha256(Path(learning_config)),
            "reuse": "unchanged parent PCE, seed validation and prior training history only"}}
    snapshot, run_dir, output_config = Path(snapshot), Path(run_dir), Path(output_config)
    mark_private_artifact_dir(snapshot)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_file() and str(relative) not in {"manifest.json", "pce_source/base_pce.yaml"}:
            _write_once(snapshot / relative, path.read_bytes())
    base = yaml.safe_load((source / "pce_source/base_pce.yaml").read_text())
    old_prefix, new_prefix = _path(source), _path(snapshot)
    for key, value in base["paths"].items():
        if isinstance(value, str) and value.startswith(old_prefix + "/"):
            base["paths"][key] = new_prefix + value[len(old_prefix):]
    base["paths"]["run_dir"] = _path(run_dir / "pce_evaluations")
    _write_once(snapshot / "pce_source/base_pce.yaml", yaml.safe_dump(base, sort_keys=False).encode())
    _write_once(snapshot / "training_replay.json", (json.dumps(replay, ensure_ascii=False) + "\n").encode())
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["training_replay"] = {"path": "training_replay.json", "sha256": sha256(snapshot / "training_replay.json")}
    _write_once(snapshot / "manifest.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    raw["experiment_contract"] = {"identity": output_config.stem, "engineering_baseline_config": _path(Path(learning_config)),
        "purpose": "one-proposal historical-minibatch autonomous-reading subagent pilot",
        "source_proposal_sha256": replay["provenance"]["source_proposal_sha256"],
        "unchanged": ["PCE prompts/models", "official scoring", "46/15/16 split", "cap10 and token limits", "ACE counters/operations"],
        "reading": "complete file inventory; no required/optional classification",
        "validation": "seed replay and candidate selection only; never learning evidence"}
    raw["paths"].update(dataset_snapshot=_path(snapshot), safe_pce_config=_path(snapshot / "pce_source/base_pce.yaml"), run_dir=_path(run_dir))
    raw["hybrid"].update(learning_mode="codex_subagents", prompt_bundle="configs/prompts/safe_pce_hybrid_subagents_v1.yaml",
        dataset_file_sha256={name: sha256(snapshot / name) for name in ("manifest.json", "train.jsonl", "validation.jsonl")})
    raw["hybrid"].pop("prompt_bundle_sha256", None)
    budget = 2 * len(validation_ids) + 2 * len(ids)
    raw["search"].update(max_iterations=1, min_proposals=1, reflection_minibatch_size=len(ids),
                         max_metric_calls=budget, projection_metric_calls=budget)
    for key in ("remote_project_dir", "remote_task_dir"):
        raw["hpc"][key] = str(Path(raw["hpc"][key]).parent / output_config.stem)
    raw["hpc"].update(worker_config_path=_path(output_config), job_name_prefix=output_config.stem)
    _write_once(output_config, yaml.safe_dump(raw, sort_keys=False).encode())
    return output_config


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
            max_metric_calls: int | None = None,
            diagnostic_source_root: Path | None = None, diagnostic_source_manifest: Path | None = None,
            planner_prompt: Path | None = None, initial_guideline: Path | None = None):
    """User supplies task IDs; this function neither samples nor selects by R/U."""
    from src.optimization.safe_pce_learning import TASK_SEMANTICS
    if set(selection) != {"train", "validation"}:
        raise ValueError("split selection must contain exactly train and validation ID arrays")
    if (diagnostic_source_root is None) != (diagnostic_source_manifest is None):
        raise ValueError("diagnostics require both source root and source manifest")
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
    mark_private_artifact_dir(snapshot)
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
    planner_prompt_path = Path(planner_prompt) if planner_prompt is not None else (
        ROOT / "configs/prompts/safe_pce_rules_planner_v1.yaml")
    initial_guideline_path = Path(initial_guideline) if initial_guideline is not None else (
        ROOT / "configs/prompts/safe_pce_hybrid_seed_v1.json")
    planner_text = yaml.safe_load(planner_prompt_path.read_text())["prompts"]["plan_system"]
    if not isinstance(planner_text, str) or not planner_text.strip():
        raise ValueError("Planner prompt must provide a nonempty prompts.plan_system")
    if not initial_guideline_path.is_file():
        raise ValueError("Initial guideline must identify an existing versioned file")
    source_manifest = base.dataset_snapshot / "manifest.json"
    manifest = json.loads(source_manifest.read_text())
    if diagnostic_source_root is not None and mode != "swe_rpg_pce":
        raise ValueError("original learning diagnostics are currently supported only for RPG")
    instances = Path(manifest.get("instances_file", "instances.jsonl"))
    if instances.is_absolute() or ".." in instances.parts:
        raise ValueError("PCE instances_file must be a safe source-relative path")
    source_dir = snapshot / "pce_source"
    for source, target in ((source_manifest, source_dir / "manifest.json"),
                           (base.dataset_snapshot / instances, source_dir / instances),
                           (base.image_manifest, source_dir / "images.json")):
        _write_once(target, source.read_bytes())
    for relative, digest in manifest.get("evaluator_files", {}).items():
        from src.pce_dataset import frozen_source_file
        source = frozen_source_file(base.dataset_snapshot, relative, digest)
        _write_once(source_dir / relative, source.read_bytes())
    bundled = resolved_pce_payload(base)
    bundled["prompts"]["plan_system"] = planner_text
    # The same source bytes keep existing source/row/image hash verification.
    bundled["paths"]["dataset_snapshot"] = _path(source_dir)
    bundled["paths"]["image_manifest"] = _path(source_dir / "images.json")
    for key in ("selection_manifest", "dependency_cache_manifest"):
        if bundled["paths"].get(key):
            source = Path(bundled["paths"][key])
            target = source_dir / f"{key}{source.suffix}"
            _write_once(target, source.read_bytes())
            bundled["paths"][key] = _path(target)
    if mode != "polybench_pce":
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
    if planner_prompt is not None:
        split_manifest["planner_prompt_source"] = {
            "path": _path(planner_prompt_path), "sha256": sha256(planner_prompt_path)}
    if initial_guideline is not None:
        split_manifest["initial_guideline_source"] = {
            "path": _path(initial_guideline_path), "sha256": sha256(initial_guideline_path)}
    if diagnostic_source_root is not None:
        from .learning_diagnostics import prepare_rpg_diagnostics
        split_manifest["learning_diagnostics"] = prepare_rpg_diagnostics(
            source_root=diagnostic_source_root, source_manifest=diagnostic_source_manifest,
            train_ids=selection["train"], snapshot=snapshot, source_revision=manifest["revision"])
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
                    "initial_guideline": _path(initial_guideline_path), "run_dir": _path(run_dir)}
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
