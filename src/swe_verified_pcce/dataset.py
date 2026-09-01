"""Pair new SWE-Verified PCE plans with one frozen evaluation membership."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.swe_verified_pcce.config import SWEVerifiedPCCEConfig
from src.swe_verified_pcce.models import PCCECase
from src.swe_verified_pce.dataset import file_sha256, load_swe_verified_pce_cases


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_pcce_cases(
    config: SWEVerifiedPCCEConfig,
) -> tuple[list[PCCECase], dict[str, Any]]:
    source_cases, source_manifest, image_manifest = load_swe_verified_pce_cases(
        config.source_snapshot,
        config.image_manifest,
    )
    image_manifest_sha256 = file_sha256(config.image_manifest)
    expected_images = getattr(config, "expected_image_manifest_sha256", None)
    if expected_images is not None and expected_images != image_manifest_sha256:
        raise ValueError("PCCE image manifest differs from its frozen SHA-256")
    source_manifest_path = config.source_snapshot / "manifest.json"
    selection = json.loads(config.selection_manifest.read_text(encoding="utf-8"))
    declared_source = selection.get("source_manifest_sha256")
    if declared_source != file_sha256(source_manifest_path):
        raise ValueError("selection manifest source identity differs")
    declared_images = selection.get("image_manifest_sha256")
    if declared_images is not None and declared_images != file_sha256(
        config.image_manifest
    ):
        raise ValueError("selection manifest image identity differs")
    image_selection = image_manifest.get("selection_manifest_sha256")
    if image_selection is not None and image_selection != file_sha256(
        config.selection_manifest
    ):
        raise ValueError("image manifest belongs to another selection")

    pce_outcomes_sha256 = file_sha256(config.pce_outcomes)
    expected_outcomes = getattr(config, "expected_pce_outcomes_sha256", None)
    if expected_outcomes is not None and expected_outcomes != pce_outcomes_sha256:
        raise ValueError("PCCE PCE outcomes differ from their frozen SHA-256")
    outcomes = _jsonl(config.pce_outcomes)
    outcome_by_id: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        instance_id = str(outcome.get("instance_id", ""))
        if not instance_id or instance_id in outcome_by_id:
            raise ValueError(f"duplicate or empty PCE outcome identity: {instance_id}")
        outcome_by_id[instance_id] = outcome
    source_by_id = {case.instance_id: case for case in source_cases}

    cases: list[PCCECase] = []
    for instance_id in config.instance_ids:
        if instance_id not in source_by_id:
            raise ValueError(f"PCCE selected source/image is missing: {instance_id}")
        if instance_id not in outcome_by_id:
            raise ValueError(f"PCCE paired PCE outcome is missing: {instance_id}")
        source = source_by_id[instance_id]
        outcome = outcome_by_id[instance_id]
        if (
            outcome.get("status") != "completed"
            or outcome.get("pce_status") != "completed"
        ):
            raise ValueError(
                f"paired PCE did not produce a complete plan: {instance_id}"
            )
        if outcome.get("row_sha256") != source.row_sha256:
            raise ValueError(f"paired PCE row identity differs: {instance_id}")
        plan = outcome.get("plan")
        evaluator = outcome.get("evaluator_result")
        if not isinstance(plan, str) or not plan.strip():
            raise ValueError(f"paired PCE outcome lacks a complete plan: {instance_id}")
        resolved = (
            evaluator.get("evaluator_resolved") if isinstance(evaluator, dict) else None
        )
        if resolved is not None and not isinstance(resolved, bool):
            raise ValueError(
                f"paired PCE outcome has an invalid evaluator result: {instance_id}"
            )
        outcome_hash = hashlib.sha256(
            json.dumps(
                outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        cases.append(PCCECase(source, plan.strip(), resolved, outcome_hash))

    return cases, {
        "source_manifest": source_manifest,
        "source_manifest_sha256": file_sha256(source_manifest_path),
        "image_manifest_sha256": image_manifest_sha256,
        "selection_manifest_sha256": file_sha256(config.selection_manifest),
        "pce_outcomes_sha256": pce_outcomes_sha256,
    }
