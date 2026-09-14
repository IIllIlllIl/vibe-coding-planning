#!/usr/bin/env python3
"""Build a compact file-referenced ACE snapshot from clean Safe PCE evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ASI_FIELDS = {
    "plan_trajectory": "plan_trajectory",
    "code_trajectory": "code_trajectory",
    "generated_patch": "patch",
    "evaluator_result": "evaluator_result",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rank(seed: int, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{instance_id}".encode("utf-8")).hexdigest()


def _validation_membership(
    rows: list[dict[str, Any]], *, fraction: float, seed: int
) -> set[str]:
    if not 0 < fraction < 1:
        raise ValueError("validation fraction must be between zero and one")
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["repo"], row["resolved"])].append(row)

    target = round(len(rows) * fraction)
    quotas = {key: math.floor(len(group) * fraction) for key, group in groups.items()}
    remaining = target - sum(quotas.values())
    allocation_order = sorted(
        groups,
        key=lambda key: (
            -(len(groups[key]) * fraction - quotas[key]),
            key[0],
            key[1],
        ),
    )
    for key in allocation_order[:remaining]:
        quotas[key] += 1

    selected = set()
    for key, group in groups.items():
        ordered = sorted(group, key=lambda row: _rank(seed, row["instance_id"]))
        selected.update(row["instance_id"] for row in ordered[: quotas[key]])
    if len(selected) != target:
        raise AssertionError("validation allocation did not reach its target")
    return selected


def build(
    *,
    run_root: Path,
    cleaning_dir: Path,
    output_dir: Path,
    validation_fraction: float,
    split_seed: int,
) -> None:
    if output_dir.exists():
        raise FileExistsError(f"refusing to modify existing output: {output_dir}")
    cleaning_manifest = _read_json(cleaning_dir / "manifest.json")
    retained = _read_jsonl(cleaning_dir / "training_instances.jsonl")
    if len(retained) != cleaning_manifest["retained_training_instances"]:
        raise ValueError("cleaning membership does not match its manifest")

    rows: list[dict[str, Any]] = []
    for clean in retained:
        task_path = run_root / clean["task_relative_path"]
        output_path = run_root / clean["output_relative_path"]
        task = _read_json(task_path)
        raw = output_path.read_bytes()
        output_sha256 = hashlib.sha256(raw).hexdigest()
        output = json.loads(raw)
        case = task["case"]
        instance_id = clean["instance_id"]
        if (
            task.get("instance_id") != instance_id
            or output.get("instance_id") != instance_id
            or case.get("instance_id") != instance_id
        ):
            raise ValueError(f"source identity mismatch: {instance_id}")
        if output.get("row_sha256") != clean["output_row_sha256"]:
            raise ValueError(f"cleaned output identity mismatch: {instance_id}")
        outcome = output["evaluator_result"]["task_outcome"]
        if outcome not in {"resolved", "unresolved"}:
            raise ValueError(f"nonterminal outcome in retained set: {instance_id}")
        artifact_path = str(output_path.absolute())
        references = {
            name: {
                "artifact_path": artifact_path,
                "artifact_sha256": output_sha256,
                "json_field": source_field,
            }
            for name, source_field in ASI_FIELDS.items()
        }
        rows.append(
            {
                "schema_version": 1,
                "dataset": "SWE-bench/SWE-bench_Verified",
                "instance_id": instance_id,
                "repo": case["repo"],
                "base_commit": case["base_commit"],
                "difficulty": case.get("difficulty"),
                "resolved": outcome == "resolved",
                "split": "pending",
                "checker_input": {
                    "issue_description": case["issue_description"],
                    "plan": output["plan"],
                    "repository": {
                        "repo": case["repo"],
                        "base_commit": case["base_commit"],
                        "instance_id": instance_id,
                    },
                },
                "asi": references,
                "source": {
                    "safe_pce_output_path": artifact_path,
                    "safe_pce_output_sha256": output_sha256,
                    "safe_pce_output_row_sha256": output["row_sha256"],
                    "cleaning_action": clean["action"],
                },
            }
        )

    validation_ids = _validation_membership(
        rows, fraction=validation_fraction, seed=split_seed
    )
    for row in rows:
        row["split"] = (
            "validation" if row["instance_id"] in validation_ids else "train"
        )
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]

    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "cases.jsonl", rows)
    _write_jsonl(output_dir / "train.jsonl", train)
    _write_jsonl(output_dir / "validation.jsonl", validation)
    manifest = {
        "schema_version": 1,
        "complete": True,
        "provisional": False,
        "immutable": True,
        "snapshot_policy": "safe-pce-file-referenced-playbook-v1",
        "dataset_record_boundary": "issue_plan_and_repository_identity",
        "checker_runtime_boundary": "issue_plan_and_visible_playbook_only",
        "reflection_boundary": (
            "historical fields are immutable raw-output references materialized "
            "only into repository-free Reflection evidence bundles"
        ),
        "source_run_root": str(run_root.absolute()),
        "source_run_manifest_sha256": _sha256(run_root / "run_manifest.json"),
        "source_cleaning_dir": str(cleaning_dir.absolute()),
        "source_cleaning_manifest_sha256": _sha256(cleaning_dir / "manifest.json"),
        "selected_instances": len(rows),
        "train_instances": len(train),
        "validation_instances": len(validation),
        "resolved": sum(row["resolved"] for row in rows),
        "unresolved": sum(not row["resolved"] for row in rows),
        "train_resolved": sum(row["resolved"] for row in train),
        "train_unresolved": sum(not row["resolved"] for row in train),
        "validation_resolved": sum(row["resolved"] for row in validation),
        "validation_unresolved": sum(not row["resolved"] for row in validation),
        "repository_distribution": dict(sorted(Counter(row["repo"] for row in rows).items())),
        "split_policy": "repo_outcome_stratified_hamilton_hash_rank_v1",
        "validation_fraction": validation_fraction,
        "split_seed": split_seed,
        "artifacts": {},
    }
    for name in ("cases.jsonl", "train.jsonl", "validation.jsonl"):
        manifest["artifacts"][name] = _sha256(output_dir / name)
    _write_json(output_dir / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--cleaning-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--split-seed", type=int, default=42)
    args = parser.parse_args()
    build(
        run_root=args.run_root.absolute(),
        cleaning_dir=args.cleaning_dir.absolute(),
        output_dir=args.output.absolute(),
        validation_fraction=args.validation_fraction,
        split_seed=args.split_seed,
    )


if __name__ == "__main__":
    main()
