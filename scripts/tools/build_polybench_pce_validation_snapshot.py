#!/usr/bin/env python3
"""Build a label-independent PolyBench validation snapshot from raw PCE evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.plan_cleaning import placeholder_reason  # noqa: E402
from src.polybench_pce.dataset import canonical_image_ref  # noqa: E402


DATASET = "AmazonScience/SWE-PolyBench"
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


def _output_dir(run_dir: Path, fingerprint: str) -> Path:
    batch = run_dir / "hpc_tasks" / "pce" / fingerprint
    output_dir = batch / "outputs"
    if not output_dir.is_dir():
        raise ValueError(f"PCE output directory is missing: {output_dir}")
    return output_dir


def _source_rows(snapshot: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest = _read_json(snapshot / "manifest.json")
    if (
        manifest.get("dataset") != DATASET
        or not manifest.get("complete")
        or manifest.get("provisional")
    ):
        raise ValueError("source snapshot is not a complete PolyBench snapshot")
    rows_path = snapshot / str(manifest.get("instances_file", "instances.jsonl"))
    if _sha256(rows_path) != manifest.get("instances_sha256"):
        raise ValueError("source instance file differs from its frozen hash")
    rows: dict[str, dict[str, Any]] = {}
    for wrapper in _read_jsonl(rows_path):
        source = wrapper.get("source_row")
        if not isinstance(source, dict):
            raise ValueError("source wrapper lacks source_row")
        instance_id = str(source.get("instance_id", ""))
        if not instance_id or instance_id in rows:
            raise ValueError("source instance IDs must be present and unique")
        canonical = json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if wrapper.get("row_sha256") != _text_sha256(canonical):
            raise ValueError(f"{instance_id}: source row hash mismatch")
        rows[instance_id] = wrapper
    if len(rows) != int(manifest.get("instances", -1)):
        raise ValueError("source instance count differs from manifest")
    return rows, manifest


def _source_exclusion(
    *,
    output: dict[str, Any],
    output_path: Path,
    repository_root: Path,
) -> dict[str, Any]:
    evaluator = output.get("evaluator_result")
    reason = evaluator.get("outcome_reason") if isinstance(evaluator, dict) else None
    if reason == "test_execution_timeout":
        reason_code = "TEST_EXECUTION_TIMEOUT"
    else:
        reason_code = "PCE_INCOMPLETE"
    return {
        "instance_id": str(output["instance_id"]),
        "reason": "PCE did not produce a parsed test outcome",
        "reason_code": reason_code,
        "pce_status": output.get("status"),
        "terminal_phase": output.get("terminal_phase"),
        "terminal_reason": output.get("terminal_reason"),
        "attempt": output.get("attempt"),
        "source_output_path": _relative(output_path, repository_root),
        "source_output_sha256": _sha256(output_path),
    }


def build_snapshot(
    *,
    source_snapshot: Path,
    pce_run: Path,
    output_dir: Path,
    repository_root: Path,
    expected_source_instances: int | None = None,
    expected_test_parsed_instances: int | None = None,
) -> dict[str, Any]:
    source_snapshot = source_snapshot.resolve()
    pce_run = pce_run.resolve()
    repository_root = repository_root.resolve()
    run_manifest_path = pce_run / "run_manifest.json"
    run_manifest = _read_json(run_manifest_path)
    if run_manifest.get("mode") != "polybench_pce":
        raise ValueError("run manifest is not PolyBench PCE")
    fingerprint = str(run_manifest.get("execution_fingerprint", ""))
    if not fingerprint:
        raise ValueError("PCE run lacks execution fingerprint")
    source_rows, source_manifest = _source_rows(source_snapshot)
    source_manifest_path = source_snapshot / "manifest.json"
    if _sha256(source_manifest_path) != run_manifest.get("dataset_manifest_sha256"):
        raise ValueError("PCE run and source snapshot manifests differ")
    if source_manifest.get("revision") != run_manifest.get("dataset_revision"):
        raise ValueError("PCE run and source dataset revisions differ")
    ordered_ids = [str(item) for item in run_manifest.get("instance_ids", [])]
    if ordered_ids != list(source_rows):
        raise ValueError("PCE run and source snapshot instance order differ")
    if expected_source_instances is not None and len(ordered_ids) != expected_source_instances:
        raise ValueError("unexpected source instance count")

    outputs_dir = _output_dir(pce_run, fingerprint)
    output_paths = sorted(outputs_dir.glob("task_*.json"))
    if len(output_paths) != len(ordered_ids):
        raise ValueError("PCE output count differs from source instance count")
    outputs: dict[str, tuple[dict[str, Any], Path]] = {}
    for output_path in output_paths:
        output = _read_json(output_path)
        task_path = outputs_dir.parent / "tasks" / output_path.name
        task = _read_json(task_path)
        instance_id = str(output.get("instance_id", ""))
        if instance_id not in source_rows or instance_id in outputs:
            raise ValueError(f"unexpected or duplicate PCE output: {instance_id}")
        if output.get("fingerprint") != fingerprint or task.get("fingerprint") != fingerprint:
            raise ValueError(f"{instance_id}: PCE fingerprint mismatch")
        task_case = task.get("case")
        if not isinstance(task_case, dict) or task.get("instance_id") != instance_id:
            raise ValueError(f"{instance_id}: PCE task identity mismatch")
        expected_row_sha256 = source_rows[instance_id].get("row_sha256")
        if task_case.get("row_sha256") != expected_row_sha256:
            raise ValueError(f"{instance_id}: source row identity mismatch")
        output_row_sha256 = output.get("row_sha256")
        if output_row_sha256 is not None and output_row_sha256 != expected_row_sha256:
            raise ValueError(f"{instance_id}: output row identity mismatch")
        outputs[instance_id] = (output, output_path)
    if set(outputs) != set(ordered_ids):
        raise ValueError("PCE outputs do not cover the frozen instance universe")

    raw_cases: list[dict[str, Any]] = []
    source_exclusions: list[dict[str, Any]] = []
    cleaning_exclusions: list[dict[str, Any]] = []
    for instance_id in ordered_ids:
        wrapper = source_rows[instance_id]
        source = dict(wrapper["source_row"])
        output, output_path = outputs[instance_id]
        evaluator = output.get("evaluator_result")
        reason = evaluator.get("outcome_reason") if isinstance(evaluator, dict) else None
        if reason not in TEST_PARSED_REASONS:
            source_exclusions.append(
                _source_exclusion(
                    output=output,
                    output_path=output_path,
                    repository_root=repository_root,
                )
            )
            continue
        if output.get("status") != "completed" or not isinstance(evaluator, dict):
            raise ValueError(f"{instance_id}: parsed outcome is not completed")
        resolved = TEST_PARSED_REASONS[str(reason)]
        if evaluator.get("task_outcome") != ("resolved" if resolved else "unresolved"):
            raise ValueError(f"{instance_id}: parsed outcome label mismatch")
        official = evaluator.get("official_score")
        if not isinstance(official, dict) or official.get("resolved") is not resolved:
            raise ValueError(f"{instance_id}: official score label mismatch")
        plan = output.get("plan")
        if not isinstance(plan, str):
            raise ValueError(f"{instance_id}: PCE plan is missing")
        patch = output.get("patch")
        if not isinstance(patch, str):
            raise ValueError(f"{instance_id}: PCE patch is missing")
        image_name = canonical_image_ref(instance_id)
        case = {
            "instance_id": instance_id,
            "split": "validation",
            "resolved": resolved,
            "task_category": str(source.get("task_category", "")),
            "language": str(source.get("language", "")),
            "checker_input": {
                "issue_description": str(source["problem_statement"]),
                "plan": plan,
                "repository": {
                    "repo": str(source["repo"]),
                    "base_commit": str(source["base_commit"]),
                    "instance_id": instance_id,
                    "dataset_type": "polybench",
                    "image_name": image_name,
                },
            },
            "source": {
                "row_sha256": str(wrapper["row_sha256"]),
                "pce_output_path": _relative(output_path, repository_root),
                "pce_output_sha256": _sha256(output_path),
                "pce_execution_fingerprint": fingerprint,
                "pce_attempt": output.get("attempt"),
                "pce_outcome_reason": reason,
                "plan_sha256": _text_sha256(plan),
                "patch_sha256": _text_sha256(patch),
                "patch_nonempty": bool(patch.strip()),
            },
        }
        raw_cases.append(case)
        placeholder = placeholder_reason(plan)
        if resolved and patch.strip() and placeholder:
            cleaning_exclusions.append(
                {
                    "instance_id": instance_id,
                    "reason": "resolved placeholder plan",
                    "reason_code": placeholder,
                    "plan_sha256": _text_sha256(plan),
                    "source_output_path": _relative(output_path, repository_root),
                    "source_output_sha256": _sha256(output_path),
                }
            )

    if (
        expected_test_parsed_instances is not None
        and len(raw_cases) != expected_test_parsed_instances
    ):
        raise ValueError("unexpected test-parsed instance count")
    excluded_ids = {item["instance_id"] for item in cleaning_exclusions}
    cleaned_cases = [
        case for case in raw_cases if case["instance_id"] not in excluded_ids
    ]

    temporary = output_dir.with_name(output_dir.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    raw_path = temporary / "raw_validation.jsonl"
    validation_path = temporary / "validation.jsonl"
    exclusions_path = temporary / "exclusions.json"
    source_exclusions_path = temporary / "source_exclusions.json"
    _write_jsonl(raw_path, raw_cases)
    _write_jsonl(validation_path, cleaned_cases)
    _write_json(exclusions_path, cleaning_exclusions)
    _write_json(source_exclusions_path, source_exclusions)

    raw_resolved = sum(bool(case["resolved"]) for case in raw_cases)
    cleaned_resolved = sum(bool(case["resolved"]) for case in cleaned_cases)
    manifest = {
        "schema_version": 1,
        "snapshot_id": output_dir.name,
        "complete": True,
        "provisional": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "immutable": True,
        "dataset": DATASET,
        "dataset_type": "polybench",
        "dataset_revision": source_manifest.get("revision"),
        "language": "Python",
        "selection_policy": "official-v1.1-image-available-and-test-parsed-v1",
        "source_exclusion_policy": "non-test-parsed-pce-outcome-v1",
        "cleaning_policy": "resolved-placeholder-high-precision-v1",
        "source": {
            "instances": len(ordered_ids),
            "resolved": None,
            "source_snapshot": _relative(source_snapshot, repository_root),
            "source_manifest_sha256": _sha256(source_manifest_path),
            "pce_run": _relative(pce_run, repository_root),
            "pce_run_manifest_sha256": _sha256(run_manifest_path),
            "pce_execution_fingerprint": fingerprint,
            "project_git_head": run_manifest.get("project_git_head"),
        },
        "raw": {
            "instances": len(raw_cases),
            "resolved": raw_resolved,
            "unresolved": len(raw_cases) - raw_resolved,
        },
        "cleaned": {
            "instances": len(cleaned_cases),
            "resolved": cleaned_resolved,
            "unresolved": len(cleaned_cases) - cleaned_resolved,
        },
        "source_excluded_instances": len(source_exclusions),
        "source_exclusion_reason_counts": dict(
            sorted(Counter(item["reason_code"] for item in source_exclusions).items())
        ),
        "cleaning_excluded_instances": len(cleaning_exclusions),
        "cleaning_exclusion_reason_counts": dict(
            sorted(Counter(item["reason_code"] for item in cleaning_exclusions).items())
        ),
        "raw_instance_ids_sha256": _text_sha256(
            "\n".join(str(case["instance_id"]) for case in raw_cases)
        ),
        "validation_instance_ids_sha256": _text_sha256(
            "\n".join(str(case["instance_id"]) for case in cleaned_cases)
        ),
        "raw_validation_file": raw_path.name,
        "raw_validation_sha256": _sha256(raw_path),
        "validation_file": validation_path.name,
        "validation_sha256": _sha256(validation_path),
        "exclusions_file": exclusions_path.name,
        "exclusions_sha256": _sha256(exclusions_path),
        "source_exclusions_file": source_exclusions_path.name,
        "source_exclusions_sha256": _sha256(source_exclusions_path),
    }
    _write_json(temporary / "manifest.json", manifest)
    if output_dir.exists():
        existing = _read_json(output_dir / "manifest.json")
        for field, filename_field in (
            ("raw_validation_sha256", "raw_validation_file"),
            ("validation_sha256", "validation_file"),
            ("exclusions_sha256", "exclusions_file"),
            ("source_exclusions_sha256", "source_exclusions_file"),
        ):
            current_path = output_dir / str(existing.get(filename_field, ""))
            if not current_path.is_file() or _sha256(current_path) != existing.get(field):
                shutil.rmtree(temporary)
                raise ValueError(f"existing validation snapshot differs: {output_dir}")
        comparable = {key: value for key, value in manifest.items() if key != "created_at"}
        old = {key: value for key, value in existing.items() if key != "created_at"}
        shutil.rmtree(temporary)
        if comparable != old:
            raise ValueError(f"existing validation snapshot differs: {output_dir}")
        return existing
    temporary.replace(output_dir)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--pce-run", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--expected-source-instances", type=int)
    parser.add_argument("--expected-test-parsed-instances", type=int)
    args = parser.parse_args()
    manifest = build_snapshot(
        source_snapshot=args.source_snapshot,
        pce_run=args.pce_run,
        output_dir=args.output_dir,
        repository_root=args.repository_root,
        expected_source_instances=args.expected_source_instances,
        expected_test_parsed_instances=args.expected_test_parsed_instances,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
