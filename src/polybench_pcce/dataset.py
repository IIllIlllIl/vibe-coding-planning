"""Join frozen PolyBench source, PCE plans, and validation membership."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.polybench_pcce.config import PolyBenchPCCEConfig
from src.polybench_pcce.models import PCCECase
from src.polybench_pce.dataset import file_sha256, load_polybench_pce_cases


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_pcce_cases(
    config: PolyBenchPCCEConfig,
) -> tuple[list[PCCECase], dict[str, Any]]:
    source_cases, source_manifest, _ = load_polybench_pce_cases(
        config.source_snapshot,
        config.image_manifest,
    )
    validation_manifest_path = config.validation_snapshot / "manifest.json"
    validation_manifest = json.loads(
        validation_manifest_path.read_text(encoding="utf-8")
    )
    if not validation_manifest.get("complete") or validation_manifest.get(
        "provisional"
    ):
        raise ValueError(
            "PCCE requires a complete, non-provisional validation snapshot"
        )
    validation_path = config.validation_snapshot / config.validation_file
    expected = validation_manifest.get("validation_sha256")
    if expected and file_sha256(validation_path) != expected:
        raise ValueError("PCCE validation file differs from its frozen manifest")
    validation_rows = _jsonl(validation_path)
    validation_ids = [str(row["instance_id"]) for row in validation_rows]
    if len(set(validation_ids)) != len(validation_ids):
        raise ValueError("PCCE validation instance IDs must be unique")
    if config.instance_ids:
        available_ids = set(validation_ids)
        missing = sorted(set(config.instance_ids) - available_ids)
        if missing:
            raise ValueError(
                "PCCE selected IDs are outside the frozen validation set: "
                + ", ".join(missing)
            )
        selected = set(config.instance_ids)
        validation_ids = [
            instance_id for instance_id in validation_ids if instance_id in selected
        ]

    outcomes = _jsonl(config.pce_outcomes)
    outcome_by_id: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        instance_id = str(outcome.get("instance_id", ""))
        if instance_id in outcome_by_id:
            raise ValueError(f"duplicate historical PCE outcome: {instance_id}")
        outcome_by_id[instance_id] = outcome
    source_by_id = {case.instance_id: case for case in source_cases}
    cases: list[PCCECase] = []
    for instance_id in validation_ids:
        if instance_id not in source_by_id or instance_id not in outcome_by_id:
            raise ValueError(f"PCCE paired input is missing: {instance_id}")
        source = source_by_id[instance_id]
        outcome = outcome_by_id[instance_id]
        if (
            outcome.get("status") != "completed"
            or outcome.get("pce_status") != "completed"
        ):
            raise ValueError(f"historical PCE outcome is incomplete: {instance_id}")
        if outcome.get("row_sha256") != source.row_sha256:
            raise ValueError(f"historical PCE row identity differs: {instance_id}")
        plan = outcome.get("plan")
        evaluator = outcome.get("evaluator_result")
        if (
            not isinstance(plan, str)
            or not plan.strip()
            or not isinstance(evaluator, dict)
        ):
            raise ValueError(
                f"historical PCE outcome lacks plan/evaluator: {instance_id}"
            )
        resolved = evaluator.get("evaluator_resolved")
        if not isinstance(resolved, bool):
            raise ValueError(
                f"historical PCE outcome lacks a boolean result: {instance_id}"
            )
        outcome_hash = hashlib.sha256(
            json.dumps(
                outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        cases.append(PCCECase(source, plan.strip(), resolved, outcome_hash))
    return cases, {
        "source_manifest": source_manifest,
        "validation_manifest": validation_manifest,
        "validation_manifest_sha256": file_sha256(validation_manifest_path),
        "validation_file_sha256": file_sha256(validation_path),
        "pce_outcomes_sha256": file_sha256(config.pce_outcomes),
    }
