"""Apply a frozen paired-rule Checker to cleaned, repeated PolyBench PCE Plans.

This is a first-review gate: no Checker feedback is sent to a Planner. Accepted
Plans reuse their own PCE Code/Evaluate outcome; rejected Plans are blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import yaml

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import TaskAttemptsExhausted, atomic_json
from src.optimization.paired_playbook import validate_paired_checker_result
from src.optimization.playbook import RejectPlaybook
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.repo_playbook import render_concern_playbook
from src.polybench_pce.dataset import file_sha256, load_polybench_pce_cases
from src.polybench_pce.hpc_executor import pce_unit_id


@dataclass(frozen=True)
class PairedGateConfig:
    config_path: Path
    source_snapshot: Path
    image_manifest: Path
    source_selection_manifest: Path
    pce_runtime_config: Path
    pce_run_manifest: Path
    pce_outcomes: Path
    eligibility_manifest: Path
    guideline: Path
    run_dir: Path
    expected_source_cases: int
    repetitions: int
    hpc: HPCConfig
    gate_selection_manifest: Path | None = None
    expected_selected_source_cases: int | None = None


def load_paired_gate_config(path: str | Path) -> PairedGateConfig:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "polybench_pcce_paired_gate":
        raise ValueError("paired gate requires mode: polybench_pcce_paired_gate")
    root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()

    def resolve(value: str) -> Path:
        expanded = Path(os.path.expandvars(value)).expanduser()
        return expanded if expanded.is_absolute() else root / expanded

    paths = raw["paths"]
    inputs = raw["inputs"]
    gate = raw["gate"]
    expected_source_cases = int(gate["expected_source_cases"])
    repetitions = int(gate["repetitions"])
    if expected_source_cases < 1 or repetitions < 1:
        raise ValueError("paired gate case and repetition counts must be positive")
    selection_path = paths.get("gate_selection_manifest")
    selected_count = gate.get("selected_source_cases")
    if (selection_path is None) != (selected_count is None):
        raise ValueError("paired gate selection path and selected case count must be provided together")
    if selected_count is not None and int(selected_count) < 1:
        raise ValueError("paired gate selected case count must be positive")
    for key in ("prompt_bundle", "repo_checker_contract", "pce_runtime_config"):
        artifact = resolve(str(inputs[key]))
        if file_sha256(artifact) != inputs[f"{key}_sha256"]:
            raise ValueError(f"paired gate {key} differs from frozen hash")
    guideline = resolve(str(paths["guideline"]))
    if file_sha256(guideline) != inputs["guideline_sha256"]:
        raise ValueError("paired gate C6 guideline differs from frozen hash")
    if raw["repo_checker"] != {
        "output_contract": "binary_v2",
        "review_only": True,
        "workdir": "/testbed",
        "command_timeout_seconds": 1800,
        "source_access_policy": "conservative_blacklist_v3",
        "history_policy": "base_ancestor_bundle_v1",
        "masked_container_paths": ["/opt/miniconda3/pkgs"],
        "observation_contract": "executed_tools_v1",
    }:
        raise ValueError("paired gate Checker must retain the frozen GEPA boundary")
    checker = raw["models"]["checker"]
    if (
        checker.get("executor") != "mini_swe"
        or checker.get("model") != "deepseek-flash"
        or checker.get("thinking") != "disabled"
        or checker.get("temperature") != 0.0
    ):
        raise ValueError("paired gate Checker differs from the frozen C6 run")
    hpc_raw = raw["hpc"]
    defaults = HPCConfig()
    hpc = replace(
        defaults,
        submit=bool(hpc_raw.get("submit", False)),
        partition=str(hpc_raw.get("partition", "batch")),
        cpus_per_task=int(hpc_raw.get("cpus_per_task", 1)),
        mem=str(hpc_raw.get("mem", "4G")),
        time=str(hpc_raw.get("time", "01:00:00")),
        max_task_attempts=int(hpc_raw.get("max_task_attempts", 3)),
        max_running_array_tasks=int(hpc_raw.get("max_running_array_tasks", 12)),
        remote_env_file=str(hpc_raw.get("remote_env_file", defaults.remote_env_file)),
        python_module=str(hpc_raw.get("python_module", defaults.python_module)),
        container_module=str(hpc_raw.get("container_module", defaults.container_module)),
        python_bin=str(hpc_raw.get("python_bin", defaults.python_bin)),
        job_name_prefix=str(hpc_raw.get("job_name_prefix", "polybench-pcce-c6-gate")),
        poll_interval_seconds=int(hpc_raw.get("poll_interval_seconds", 300)),
        task_output_grace_seconds=int(hpc_raw.get("task_output_grace_seconds", 300)),
        missing_task_grace_seconds=int(hpc_raw.get("missing_task_grace_seconds", 600)),
    )
    if hpc.cpus_per_task != 1 or hpc.mem not in {"4G", "1750M"}:
        raise ValueError("paired gate workers require one CPU and an approved memory size")
    if hpc.max_task_attempts != 3:
        raise ValueError("paired gate requires three total Checker attempts")
    if hpc.max_running_array_tasks < 0:
        raise ValueError("paired gate array concurrency cannot be negative")
    return PairedGateConfig(
        config_path=config_path,
        source_snapshot=resolve(str(paths["source_snapshot"])),
        image_manifest=resolve(str(paths["image_manifest"])),
        source_selection_manifest=resolve(str(paths["source_selection_manifest"])),
        pce_runtime_config=resolve(str(inputs["pce_runtime_config"])),
        pce_run_manifest=resolve(str(paths["pce_run_manifest"])),
        pce_outcomes=resolve(str(paths["pce_outcomes"])),
        eligibility_manifest=resolve(str(paths["eligibility_manifest"])),
        guideline=guideline,
        run_dir=resolve(str(paths["run_dir"])),
        expected_source_cases=expected_source_cases,
        repetitions=repetitions,
        hpc=hpc,
        gate_selection_manifest=resolve(str(selection_path)) if selection_path else None,
        expected_selected_source_cases=int(selected_count) if selected_count is not None else None,
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_gate_units(config: PairedGateConfig) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, str]]:
    cases, _, _ = load_polybench_pce_cases(config.source_snapshot, config.image_manifest)
    by_source = {case.instance_id: case for case in cases}
    pce_manifest = json.loads(config.pce_run_manifest.read_text(encoding="utf-8"))
    source_hash = file_sha256(config.source_snapshot / "manifest.json")
    image_hash = file_sha256(config.image_manifest)
    if (
        pce_manifest.get("dataset_manifest_sha256") != source_hash
        or pce_manifest.get("image_manifest_sha256") != image_hash
        or pce_manifest.get("selection_manifest_sha256") != file_sha256(config.source_selection_manifest)
        or pce_manifest.get("config_sha256") != file_sha256(config.pce_runtime_config)
    ):
        raise ValueError("PCE source, selection, or image authority differs from paired gate")
    source_ids = pce_manifest.get("instance_ids", [])
    if (
        pce_manifest.get("repetitions") != config.repetitions
        or len(source_ids) != config.expected_source_cases
        or len(set(source_ids)) != len(source_ids)
        or not set(source_ids).issubset(by_source)
    ):
        raise ValueError("paired gate source count or repetition differs from PCE")
    expected_units = {
        pce_unit_id(str(unit["source_instance_id"]), int(unit["repetition"]), config.repetitions)
        for unit in pce_manifest["execution_units"]
    }
    declared_units = {
        pce_unit_id(source_id, repetition, config.repetitions)
        for source_id in source_ids
        for repetition in range(1, config.repetitions + 1)
    }
    if expected_units != declared_units or len(pce_manifest["execution_units"]) != len(declared_units):
        raise ValueError("PCE execution units differ from the declared source cases")
    outcomes = _jsonl(config.pce_outcomes)
    by_unit = {str(row.get("instance_id")): row for row in outcomes}
    if len(by_unit) != len(outcomes) or set(by_unit) != expected_units:
        raise ValueError("PCE outcome units differ from the frozen 77-by-2 run")
    for unit_id, outcome in by_unit.items():
        source_id = outcome.get("source_instance_id")
        repetition = outcome.get("repetition")
        if (
            source_id not in source_ids
            or type(repetition) is not int
            or repetition not in range(1, config.repetitions + 1)
            or unit_id != pce_unit_id(source_id, repetition, config.repetitions)
        ):
            raise ValueError(f"PCE outcome identity differs: {unit_id}")
    eligibility = json.loads(config.eligibility_manifest.read_text(encoding="utf-8"))
    if (
        eligibility.get("schema_version") != 1
        or eligibility.get("source_pce_run_manifest_sha256") != file_sha256(config.pce_run_manifest)
        or eligibility.get("source_pce_outcomes_sha256") != file_sha256(config.pce_outcomes)
    ):
        raise ValueError("eligibility manifest is not bound to this PCE evidence")
    selected = eligibility.get("selected_unit_ids")
    excluded = eligibility.get("excluded_unit_reasons")
    if not isinstance(selected, list) or not isinstance(excluded, dict):
        raise ValueError("eligibility manifest requires selected IDs and exclusion reasons")
    if not selected or len(set(selected)) != len(selected) or set(selected) & set(excluded):
        raise ValueError("eligibility unit IDs overlap or repeat")
    if set(selected) | set(excluded) != expected_units:
        raise ValueError("eligibility must account for every PCE execution unit")
    if any(not isinstance(reason, str) or not reason.strip() for reason in excluded.values()):
        raise ValueError("every excluded unit requires a reviewable reason")
    chosen_sources: list[str] | None = None
    selection_hash: str | None = None
    if config.gate_selection_manifest is not None:
        if config.repetitions != 2:
            raise ValueError("paired gate source strata require exactly two repetitions")
        selection_hash = file_sha256(config.gate_selection_manifest)
        gate_selection = json.loads(config.gate_selection_manifest.read_text(encoding="utf-8"))
        groups = gate_selection.get("source_groups")
        if (
            gate_selection.get("schema_version") != 1
            or gate_selection.get("source_eligibility_manifest_sha256") != file_sha256(config.eligibility_manifest)
            or gate_selection.get("source_pce_outcomes_sha256") != file_sha256(config.pce_outcomes)
            or not isinstance(groups, dict)
            or set(groups) != {"RR", "UU", "RU"}
            or any(not isinstance(value, list) for value in groups.values())
        ):
            raise ValueError("paired gate selection differs from frozen PCE eligibility")
        chosen_sources = [source for group in ("RR", "UU", "RU") for source in groups[group]]
        if (
            not chosen_sources
            or len(chosen_sources) != config.expected_selected_source_cases
            or len(set(chosen_sources)) != len(chosen_sources)
            or any(not isinstance(source, str) or not source for source in chosen_sources)
        ):
            raise ValueError("paired gate selected source count or identity is invalid")
        selected_set = set(selected)
        for group, sources in groups.items():
            for source in sources:
                if source not in source_ids:
                    raise ValueError(f"paired gate selected source is outside PCE: {source}")
                source_units = [pce_unit_id(source, rep, config.repetitions) for rep in range(1, config.repetitions + 1)]
                if any(unit not in selected_set for unit in source_units):
                    raise ValueError(f"paired gate selected source lacks eligible repetitions: {source}")
                labels = [by_unit[unit]["evaluator_result"]["evaluator_resolved"] for unit in source_units]
                if any(type(label) is not bool for label in labels):
                    raise ValueError(f"paired gate source lacks binary PCE outcomes: {source}")
                actual = "RR" if labels == [True, True] else "UU" if labels == [False, False] else "RU"
                if actual != group:
                    raise ValueError(f"paired gate source stratum differs from PCE outcomes: {source}")
    units = []
    for unit_id in selected:
        outcome = by_unit[unit_id]
        source_id = outcome.get("source_instance_id")
        repetition = outcome.get("repetition")
        if chosen_sources is not None and source_id not in chosen_sources:
            continue
        case = by_source[source_id]
        evaluator = outcome.get("evaluator_result")
        if (
            outcome.get("status") != "completed"
            or outcome.get("pce_status") != "completed"
            or outcome.get("row_sha256") != case.row_sha256
            or not isinstance(outcome.get("plan"), str)
            or not outcome["plan"].strip()
            or not isinstance(evaluator, dict)
            or not isinstance(evaluator.get("evaluator_resolved"), bool)
        ):
            raise ValueError(f"selected PCE unit lacks complete auditable evidence: {unit_id}")
        units.append((case, outcome))
    hashes = {
        "pce_run_manifest_sha256": file_sha256(config.pce_run_manifest),
        "pce_outcomes_sha256": file_sha256(config.pce_outcomes),
        "eligibility_sha256": file_sha256(config.eligibility_manifest),
        "source_manifest_sha256": source_hash,
        "image_manifest_sha256": image_hash,
        "source_selection_manifest_sha256": file_sha256(config.source_selection_manifest),
        "pce_runtime_config_sha256": file_sha256(config.pce_runtime_config),
    }
    if selection_hash is not None:
        hashes["gate_selection_sha256"] = selection_hash
    return units, hashes


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_paired_gate(config: PairedGateConfig) -> dict[str, Any] | None:
    units, hashes = load_gate_units(config)
    playbook = RejectPlaybook.parse(config.guideline.read_text(encoding="utf-8"))
    visible = render_concern_playbook(playbook)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pcce_paired_gate",
        "config_sha256": file_sha256(config.config_path),
        "guideline_sha256": file_sha256(config.guideline),
        "source_hashes": hashes,
        "unit_ids": [outcome["instance_id"] for _, outcome in units],
        "expected_source_cases": config.expected_source_cases,
        "repetitions": config.repetitions,
        "checker_input_boundary": "task, plan, frozen base repository, C6; no PCE outcome or feedback",
        "accepted_outcome": "reuse the same execution unit's PCE Code/Evaluate result",
        "rejected_outcome": "blocked before Code",
    }
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("paired gate run manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)
    items = []
    for case, outcome in units:
        items.append({
            "instance_id": outcome["instance_id"],
            "validation_rule_count": len(playbook.bullets),
            "output_contract": "binary_v2",
            "repository": {
                "repo": case.repo,
                "base_commit": case.base_commit,
                "instance_id": case.instance_id,
                "image_name": case.image.requested_ref,
            },
            "image_authority": {
                "requested_ref": case.image.requested_ref,
                "sif_path": case.image.sif_path,
                "sif_sha256": case.image.sif_sha256,
                "sif_bytes": case.image.sif_bytes,
            },
            "prompt_values": {
                "issue": case.issue_description,
                "plan": outcome["plan"],
                "checker_visible_playbook": visible,
                "retry_feedback": "",
            },
        })
    executor = PlaybookHPCExecutor(
        config_path=config.config_path,
        run_dir=config.run_dir,
        hpc=config.hpc,
        checker_observation_contract="executed_tools_v1",
    )
    try:
        outputs = executor.run_wave("paired_repo_checker", items)
    except ControllerYield as exc:
        atomic_json(config.run_dir / "controller_status.json", {
            "schema_version": 1, "status": "yielded", "reason": exc.reason,
            "batch_dir": exc.batch_dir, "worker_job_id": exc.job_id,
        })
        return None
    except TaskAttemptsExhausted:
        batch_dir = executor.batch_dir_for("paired_repo_checker", items)
        outputs = []
        for index, item in enumerate(items):
            path = batch_dir / "outputs" / f"task_{index:04d}.json"
            value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            outputs.append(value if value.get("status") == "completed" else {
                "status": "incomplete", "instance_id": item["instance_id"],
                "last_worker_output": value or None,
            })
    rows = []
    for (case, baseline), output in zip(units, outputs, strict=True):
        raw = output.get("agent_output") if output.get("status") == "completed" else None
        decision = (
            validate_paired_checker_result(raw, playbook, levels=False, require_reason=False)
            if isinstance(raw, dict) else None
        )
        resolved = baseline["evaluator_result"]["evaluator_resolved"]
        rows.append({
            "instance_id": baseline["instance_id"],
            "source_instance_id": case.instance_id,
            "repetition": baseline["repetition"],
            "baseline_pce_resolved": resolved,
            "checker_rejected": decision.rejected if decision else None,
            "pcce_resolved": False if decision and decision.rejected else resolved if decision else None,
            "method_status": "checker_rejected" if decision and decision.rejected else "accepted_pce_reused" if decision else "operational_incomplete",
            "triggered_rule_numbers": [i.rule_number for i in decision.rule_results if i.triggered] if decision else [],
            "checker_output": output if decision else None,
            "baseline_outcome_sha256": text_sha256(json.dumps(baseline, sort_keys=True)),
        })
    _write_jsonl(config.run_dir / "pcce_gate_outcomes.jsonl", rows)
    summary = {
        "schema_version": 1,
        "mode": "polybench_pcce_paired_gate",
        "status": "completed" if all(row["pcce_resolved"] is not None for row in rows) else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "units": len(rows),
        "accepted_pce_reused": sum(row["method_status"] == "accepted_pce_reused" for row in rows),
        "checker_rejected": sum(row["method_status"] == "checker_rejected" for row in rows),
        "operational_incomplete": sum(row["method_status"] == "operational_incomplete" for row in rows),
        "correct_accept_resolved": sum(row["checker_rejected"] is False and row["baseline_pce_resolved"] for row in rows),
        "correct_reject_unresolved": sum(row["checker_rejected"] is True and not row["baseline_pce_resolved"] for row in rows),
        "false_accept_unresolved": sum(row["checker_rejected"] is False and not row["baseline_pce_resolved"] for row in rows),
        "false_reject_resolved": sum(row["checker_rejected"] is True and row["baseline_pce_resolved"] for row in rows),
    }
    atomic_json(config.run_dir / "result.json", summary)
    atomic_json(config.run_dir / "controller_status.json", summary)
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    run_paired_gate(load_paired_gate_config(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
