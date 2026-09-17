#!/usr/bin/env python3
"""Freeze subset and recovered-Plan inputs from preserved PCE checkpoints."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--parent-selection", required=True, type=Path)
    parser.add_argument("--parent-images", required=True, type=Path)
    parser.add_argument("--source-run-manifest", type=Path)
    parser.add_argument("--checkpoint-root", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if spec.get("schema_version") != 1:
        raise ValueError("recovery spec must use schema_version 1")
    mode = str(spec.get("mode", ""))
    if mode not in {"recovered_plan_ce", "full_pce"}:
        raise ValueError("recovery mode must be recovered_plan_ce or full_pce")

    selected = spec.get("selected_instances")
    if not isinstance(selected, list) or not selected:
        raise ValueError("selected_instances must be a nonempty list")
    selected_ids = [str(row["instance_id"]) for row in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("selected instance IDs must be unique")

    source = json.loads(args.source_manifest.read_text(encoding="utf-8"))
    parent_selection = json.loads(args.parent_selection.read_text(encoding="utf-8"))
    parent_images = json.loads(args.parent_images.read_text(encoding="utf-8"))
    if parent_selection.get("source_manifest_sha256") != _file_sha256(
        args.source_manifest
    ):
        raise ValueError("parent selection belongs to another source snapshot")
    if parent_images.get("selection_manifest_sha256") != _file_sha256(
        args.parent_selection
    ):
        raise ValueError("parent images belong to another selection")

    parent_cases = {
        str(row["instance_id"]): row for row in parent_selection["selected_cases"]
    }
    missing = sorted(set(selected_ids) - set(parent_cases))
    if missing:
        raise ValueError(
            "recovery cases absent from parent selection: " + ", ".join(missing)
        )
    selected_cases = [parent_cases[instance_id] for instance_id in selected_ids]

    selection = {
        "schema_version": 1,
        "selection_id": spec["recovery_id"],
        "purpose": spec["purpose"],
        "dataset": source["dataset"],
        "dataset_revision": source["revision"],
        "source_manifest_sha256": _file_sha256(args.source_manifest),
        "source_instances_sha256": source["instances_sha256"],
        "source_instance_count": source["instances"],
        "selected_instance_ids": selected_ids,
        "selected_instance_ids_sha256": _stable_hash(selected_ids),
        "selected_cases": selected_cases,
        "selected_count": len(selected_cases),
        "repository_distribution": dict(
            sorted(Counter(row["repo"] for row in selected_cases).items())
        ),
        "selection_policy": spec["selection_policy"],
        "recovery_contract": spec["recovery_contract"],
        "source_authorities": {
            "parent_selection": str(args.parent_selection),
            "parent_selection_sha256": _file_sha256(args.parent_selection),
            "parent_images": str(args.parent_images),
            "parent_images_sha256": _file_sha256(args.parent_images),
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = args.output_dir / "selection.json"
    selection_path.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    selected_set = set(selected_ids)
    image_records = {
        key: value
        for key, value in parent_images["records"].items()
        if value["instance_id"] in selected_set
    }
    if {value["instance_id"] for value in image_records.values()} != selected_set:
        raise ValueError("parent images do not cover the recovery subset")
    if any(
        value["status"] != "audited" or not value["base_commit_verified"]
        for value in image_records.values()
    ):
        raise ValueError("recovery subset contains an unverified image")
    images = {
        "schema_version": 1,
        "purpose": "swe_verified_sif_subset_projection_for_pce_recovery",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_sha256": _file_sha256(args.source_manifest),
        "selection_manifest_sha256": _file_sha256(selection_path),
        "parent_image_manifest": str(args.parent_images),
        "parent_image_manifest_sha256": _file_sha256(args.parent_images),
        "projection_policy": "exact selected records copied without changing frozen SIF identity",
        "verify_base_commits": True,
        "records": image_records,
        "summary": {
            "records": len(image_records),
            "audited": len(image_records),
            "missing": 0,
            "base_commit_verified": len(image_records),
        },
    }
    images["manifest_id"] = _stable_hash(images)
    images_path = args.output_dir / "images.json"
    images_path.write_text(
        json.dumps(images, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    if mode == "recovered_plan_ce":
        if args.source_run_manifest is None or args.checkpoint_root is None:
            raise ValueError(
                "recovered_plan_ce requires source-run-manifest and checkpoint-root"
            )
        source_run = json.loads(args.source_run_manifest.read_text(encoding="utf-8"))
        expected_run_sha = spec["source_run"]["run_manifest_sha256"]
        if _file_sha256(args.source_run_manifest) != expected_run_sha:
            raise ValueError("source run manifest hash differs from recovery spec")
        expected_fingerprint = spec["source_run"]["execution_fingerprint"]
        if source_run.get("execution_fingerprint") != expected_fingerprint:
            raise ValueError("source execution fingerprint differs from recovery spec")

        recovered = []
        for row in selected:
            instance_id = str(row["instance_id"])
            task_index = int(row["source_task_index"])
            checkpoint_path = (
                args.checkpoint_root / f"task_{task_index:04d}" / "plan.json"
            )
            if _file_sha256(checkpoint_path) != row["plan_checkpoint_sha256"]:
                raise ValueError(f"Plan checkpoint hash differs: {instance_id}")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            payload = checkpoint.get("payload")
            plan = payload.get("plan") if isinstance(payload, dict) else None
            if checkpoint.get("phase") != "plan" or not isinstance(plan, str) or not plan:
                raise ValueError(f"invalid Plan checkpoint: {instance_id}")
            if _text_sha256(plan) != row["plan_sha256"]:
                raise ValueError(f"Plan text hash differs: {instance_id}")
            recovered.append(
                {
                    "instance_id": instance_id,
                    "plan": plan,
                    "plan_sha256": row["plan_sha256"],
                    "source_run_id": spec["source_run"]["run_id"],
                    "source_execution_fingerprint": expected_fingerprint,
                    "source_task_index": task_index,
                    "source_checkpoint_identity": checkpoint["checkpoint_identity"],
                    "source_plan_checkpoint_sha256": row["plan_checkpoint_sha256"],
                    "source_plan_trajectory_sha256": _stable_hash(
                        payload.get("trajectory", [])
                    ),
                }
            )
        replay = {
            "schema_version": 1,
            "replay_id": spec["recovery_id"],
            "purpose": "swe_verified_recovered_plan_ce_replay",
            "source_manifest_sha256": _file_sha256(args.source_manifest),
            "selection_manifest_sha256": _file_sha256(selection_path),
            "image_manifest_sha256": _file_sha256(images_path),
            "attribution": "independent_code_sampling_from_preserved_plan",
            "historical_outcome_is_not_overwritten": True,
            "source_run": spec["source_run"],
            "recovered_plans": recovered,
        }
        (args.output_dir / "replay.json").write_text(
            json.dumps(replay, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {"mode": mode, "selected": len(selected_ids), "output": str(args.output_dir)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
