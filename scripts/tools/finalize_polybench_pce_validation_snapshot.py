#!/usr/bin/env python3
"""Freeze a PCE validation snapshot after a completed evaluator-only repair."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


TEST_PARSED_REASONS = {
    "tests_parsed_resolved": True,
    "tests_parsed_unresolved": False,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _text_sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    values = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError(f"expected JSON objects: {path}")
    return values


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _verify_file(snapshot: Path, manifest: dict[str, Any], field: str) -> Path:
    filename_field = field.removesuffix("_sha256") + "_file"
    path = snapshot / str(manifest.get(filename_field, ""))
    if not path.is_file() or _sha256(path) != manifest.get(field):
        raise ValueError(f"base snapshot file differs from {field}: {path}")
    return path


def _repair_outputs(
    repair: Path,
) -> tuple[dict[str, tuple[dict[str, Any], Path]], dict[str, Any], dict[str, Any]]:
    manifest_path = repair / "run_manifest.json"
    result_path = repair / "result.json"
    manifest = _read_json(manifest_path)
    result = _read_json(result_path)
    if manifest.get("mode") != "polybench_pce_evaluator_resume":
        raise ValueError("repair manifest is not a PCE evaluator-only repair")
    if result.get("status") != "completed" or result.get("unknown") != 0:
        raise ValueError("evaluator repair is not complete and fully classified")
    selected = [str(value) for value in manifest.get("selected_instance_ids", [])]
    if len(selected) != len(set(selected)) or not selected:
        raise ValueError("repair selected instance IDs must be present and unique")
    fingerprint = str(manifest.get("repair_fingerprint", ""))
    batch = repair / "hpc_tasks" / "evaluate" / fingerprint
    task_state = _read_json(batch / "task_state.json")
    if task_state.get("phase") != "COMPLETE" or task_state.get("fingerprint") != fingerprint:
        raise ValueError("repair task batch is not complete")
    output_paths = sorted((batch / "outputs").glob("task_*.json"))
    outputs: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in output_paths:
        output = _read_json(path)
        instance_id = str(output.get("instance_id", ""))
        if instance_id not in selected or instance_id in outputs:
            raise ValueError(f"unexpected or duplicate repair output: {instance_id}")
        evaluator = output.get("evaluator_result")
        reason = evaluator.get("outcome_reason") if isinstance(evaluator, dict) else None
        if (
            output.get("status") != "completed"
            or output.get("pce_status") != "completed"
            or reason not in TEST_PARSED_REASONS
        ):
            raise ValueError(f"repair output is not completed/tests_parsed: {instance_id}")
        resolved = TEST_PARSED_REASONS[str(reason)]
        if (
            evaluator.get("evaluator_resolved") is not resolved
            or evaluator.get("task_outcome") != ("resolved" if resolved else "unresolved")
        ):
            raise ValueError(f"repair label fields disagree: {instance_id}")
        outputs[instance_id] = (output, path)
    if list(outputs) != selected:
        raise ValueError("repair outputs do not match the frozen selected order")
    resolved = sum(
        output["evaluator_result"]["evaluator_resolved"]
        for output, _ in outputs.values()
    )
    if (
        len(outputs) != result.get("evaluated_instances")
        or resolved != result.get("resolved")
        or len(outputs) - resolved != result.get("unresolved")
    ):
        raise ValueError("repair output counts differ from result.json")
    return outputs, manifest, result


def finalize_snapshot(
    *,
    base_snapshot: Path,
    evaluator_repair: Path,
    environment_exclusions: Path,
    output_dir: Path,
    repository_root: Path,
    expected_final_instances: int | None = None,
) -> dict[str, Any]:
    base_snapshot = base_snapshot.resolve()
    evaluator_repair = evaluator_repair.resolve()
    environment_exclusions = environment_exclusions.resolve()
    repository_root = repository_root.resolve()
    base_manifest_path = base_snapshot / "manifest.json"
    base_manifest = _read_json(base_manifest_path)
    if not base_manifest.get("complete") or base_manifest.get("provisional"):
        raise ValueError("base validation snapshot is not complete and non-provisional")
    raw_path = _verify_file(base_snapshot, base_manifest, "raw_validation_sha256")
    validation_path = _verify_file(base_snapshot, base_manifest, "validation_sha256")
    exclusions_path = _verify_file(base_snapshot, base_manifest, "exclusions_sha256")
    source_exclusions_path = _verify_file(
        base_snapshot, base_manifest, "source_exclusions_sha256"
    )
    paired_path = _verify_file(
        base_snapshot, base_manifest, "paired_pce_outcomes_sha256"
    )

    raw_rows = _read_jsonl(raw_path)
    validation_rows = _read_jsonl(validation_path)
    paired_rows = _read_jsonl(paired_path)
    raw_by_id = {str(row["instance_id"]): row for row in raw_rows}
    validation_by_id = {str(row["instance_id"]): row for row in validation_rows}
    paired_by_id = {str(row["instance_id"]): row for row in paired_rows}
    for name, rows, mapping in (
        ("raw", raw_rows, raw_by_id),
        ("validation", validation_rows, validation_by_id),
        ("paired", paired_rows, paired_by_id),
    ):
        if len(rows) != len(mapping):
            raise ValueError(f"base {name} instance IDs are not unique")
    if not set(validation_by_id).issubset(paired_by_id):
        raise ValueError("base paired PCE outcomes do not cover validation")

    repair_outputs, repair_manifest, repair_result = _repair_outputs(evaluator_repair)
    source_fingerprint = str(
        base_manifest.get("source", {}).get("pce_execution_fingerprint", "")
    )
    if repair_manifest.get("source_execution_fingerprint") != source_fingerprint:
        raise ValueError("repair and base PCE execution fingerprints differ")
    overlay_ids = list(repair_outputs)
    missing = sorted(set(overlay_ids) - set(validation_by_id))
    if missing:
        raise ValueError("repair cases are outside the base validation snapshot: " + ", ".join(missing))

    overlay_records: list[dict[str, Any]] = []
    for instance_id in overlay_ids:
        repair_output, repair_output_path = repair_outputs[instance_id]
        base_outcome = paired_by_id[instance_id]
        if (
            repair_output.get("row_sha256") != base_outcome.get("row_sha256")
            or repair_output.get("plan") != base_outcome.get("plan")
        ):
            raise ValueError(f"repair changed source row or Plan: {instance_id}")
        evaluator = repair_output["evaluator_result"]
        resolved = bool(evaluator["evaluator_resolved"])
        original_evaluator = base_outcome["evaluator_result"]
        overlay = {
            "repair_id": repair_manifest["repair_id"],
            "repair_fingerprint": repair_manifest["repair_fingerprint"],
            "evaluator_semantic_sha256": repair_manifest["evaluator_semantic_sha256"],
            "repair_output_path": _relative(repair_output_path, repository_root),
            "repair_output_sha256": _sha256(repair_output_path),
            "original_evaluator_result_sha256": _canonical_sha256(original_evaluator),
            "repaired_evaluator_result_sha256": _canonical_sha256(evaluator),
        }
        base_outcome["evaluator_result"] = evaluator
        base_outcome["evaluator_overlay"] = overlay
        for row in (raw_by_id[instance_id], validation_by_id[instance_id]):
            row["resolved"] = resolved
            row["source"]["pce_outcome_reason"] = evaluator["outcome_reason"]
            row["source"]["evaluator_overlay"] = overlay
        overlay_records.append({"instance_id": instance_id, **overlay})

    exclusion_spec = _read_json(environment_exclusions)
    exclusions = exclusion_spec.get("exclusions")
    if not isinstance(exclusions, list) or not exclusions:
        raise ValueError("environment exclusion specification is empty")
    excluded_ids: set[str] = set()
    for item in exclusions:
        if not isinstance(item, dict):
            raise ValueError("environment exclusions must be JSON objects")
        instance_id = str(item.get("instance_id", ""))
        if instance_id not in validation_by_id or instance_id in excluded_ids:
            raise ValueError(f"unexpected or duplicate environment exclusion: {instance_id}")
        excluded_ids.add(instance_id)
    final_validation = [
        validation_by_id[str(row["instance_id"])]
        for row in validation_rows
        if str(row["instance_id"]) not in excluded_ids
    ]
    final_paired = [
        paired_by_id[str(row["instance_id"])] for row in final_validation
    ]
    if expected_final_instances is not None and len(final_validation) != expected_final_instances:
        raise ValueError("unexpected final validation instance count")

    temporary = output_dir.with_name(output_dir.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    final_raw_path = temporary / "raw_validation.jsonl"
    final_validation_path = temporary / "validation.jsonl"
    final_paired_path = temporary / "paired_pce_outcomes.jsonl"
    final_exclusions_path = temporary / "exclusions.json"
    final_source_exclusions_path = temporary / "source_exclusions.json"
    final_environment_exclusions_path = temporary / "environment_exclusions.json"
    final_overlay_path = temporary / "evaluator_overlay.json"
    _write_jsonl(final_raw_path, [raw_by_id[str(row["instance_id"])] for row in raw_rows])
    _write_jsonl(final_validation_path, final_validation)
    _write_jsonl(final_paired_path, final_paired)
    _write_json(
        final_exclusions_path,
        json.loads(exclusions_path.read_text(encoding="utf-8")),
    )
    _write_json(
        final_source_exclusions_path,
        json.loads(source_exclusions_path.read_text(encoding="utf-8")),
    )
    _write_json(final_environment_exclusions_path, exclusion_spec)
    _write_json(final_overlay_path, overlay_records)

    raw_resolved = sum(bool(row["resolved"]) for row in raw_by_id.values())
    final_resolved = sum(bool(row["resolved"]) for row in final_validation)
    reason_counts = Counter(str(item.get("reason_code", "UNSPECIFIED")) for item in exclusions)
    manifest = {
        "schema_version": 1,
        "snapshot_id": output_dir.name,
        "complete": True,
        "provisional": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
        "dataset": base_manifest.get("dataset"),
        "dataset_type": base_manifest.get("dataset_type"),
        "dataset_revision": base_manifest.get("dataset_revision"),
        "language": base_manifest.get("language"),
        "selection_policy": "clean-pce-test-parsed-with-frozen-evaluator-overlay-v1",
        "source_exclusion_policy": base_manifest.get("source_exclusion_policy"),
        "cleaning_policy": base_manifest.get("cleaning_policy"),
        "environment_exclusion_policy": str(exclusion_spec.get("policy", "")),
        "source": base_manifest.get("source"),
        "base_snapshot": {
            "path": _relative(base_snapshot, repository_root),
            "manifest_sha256": _sha256(base_manifest_path),
            "validation_sha256": base_manifest["validation_sha256"],
            "paired_pce_outcomes_sha256": base_manifest["paired_pce_outcomes_sha256"],
        },
        "evaluator_repair": {
            "path": _relative(evaluator_repair, repository_root),
            "tree_sha256": _tree_sha256(evaluator_repair),
            "run_manifest_sha256": _sha256(evaluator_repair / "run_manifest.json"),
            "result_sha256": _sha256(evaluator_repair / "result.json"),
            "repair_id": repair_manifest["repair_id"],
            "repair_fingerprint": repair_manifest["repair_fingerprint"],
            "evaluator_semantic_sha256": repair_manifest["evaluator_semantic_sha256"],
            "source_execution_fingerprint": repair_manifest["source_execution_fingerprint"],
            "instances": len(overlay_ids),
            "resolved": repair_result["resolved"],
            "unresolved": repair_result["unresolved"],
        },
        "raw": {
            "instances": len(raw_rows),
            "resolved": raw_resolved,
            "unresolved": len(raw_rows) - raw_resolved,
        },
        "cleaned": {
            "instances": len(final_validation),
            "resolved": final_resolved,
            "unresolved": len(final_validation) - final_resolved,
        },
        "source_excluded_instances": base_manifest.get("source_excluded_instances"),
        "source_exclusion_reason_counts": base_manifest.get("source_exclusion_reason_counts"),
        "cleaning_excluded_instances": base_manifest.get("cleaning_excluded_instances"),
        "cleaning_exclusion_reason_counts": base_manifest.get("cleaning_exclusion_reason_counts"),
        "environment_excluded_instances": len(excluded_ids),
        "environment_exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "evaluator_overlay_instances": len(overlay_ids),
        "raw_instance_ids_sha256": _text_sha256(
            "\n".join(str(row["instance_id"]) for row in raw_rows)
        ),
        "validation_instance_ids_sha256": _text_sha256(
            "\n".join(str(row["instance_id"]) for row in final_validation)
        ),
        "raw_validation_file": final_raw_path.name,
        "raw_validation_sha256": _sha256(final_raw_path),
        "validation_file": final_validation_path.name,
        "validation_sha256": _sha256(final_validation_path),
        "paired_pce_outcomes_file": final_paired_path.name,
        "paired_pce_outcomes_sha256": _sha256(final_paired_path),
        "exclusions_file": final_exclusions_path.name,
        "exclusions_sha256": _sha256(final_exclusions_path),
        "source_exclusions_file": final_source_exclusions_path.name,
        "source_exclusions_sha256": _sha256(final_source_exclusions_path),
        "environment_exclusions_file": final_environment_exclusions_path.name,
        "environment_exclusions_sha256": _sha256(final_environment_exclusions_path),
        "evaluator_overlay_file": final_overlay_path.name,
        "evaluator_overlay_sha256": _sha256(final_overlay_path),
    }
    _write_json(temporary / "manifest.json", manifest)
    if output_dir.exists():
        existing_manifest_path = output_dir / "manifest.json"
        existing = _read_json(existing_manifest_path)
        for field in (
            "raw_validation_sha256",
            "validation_sha256",
            "paired_pce_outcomes_sha256",
            "exclusions_sha256",
            "source_exclusions_sha256",
            "environment_exclusions_sha256",
            "evaluator_overlay_sha256",
        ):
            _verify_file(output_dir, existing, field)
        comparable = {key: value for key, value in manifest.items() if key != "created_at"}
        old = {key: value for key, value in existing.items() if key != "created_at"}
        shutil.rmtree(temporary)
        if comparable != old:
            raise ValueError(f"existing final snapshot differs: {output_dir}")
        return existing
    temporary.replace(output_dir)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--base-snapshot", required=True, type=Path)
    parser.add_argument("--evaluator-repair", required=True, type=Path)
    parser.add_argument("--environment-exclusions", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-final-instances", type=int)
    args = parser.parse_args()
    manifest = finalize_snapshot(
        base_snapshot=args.base_snapshot,
        evaluator_repair=args.evaluator_repair,
        environment_exclusions=args.environment_exclusions,
        output_dir=args.output_dir,
        repository_root=args.repository_root,
        expected_final_instances=args.expected_final_instances,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
