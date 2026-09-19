#!/usr/bin/env python3
"""Freeze task-grouped R/U Plan pairs from audited Safe-PCE observations."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _stable_id(value: Any) -> str:
    return _sha_bytes(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"JSONL row is not an object: {path}")
                yield value


def _baseline_observation(case: dict[str, Any]) -> dict[str, Any]:
    plan = str(case["checker_input"]["plan"])
    plan_sha = _sha_bytes(plan.encode("utf-8"))
    source = case.get("source") or {}
    identity = {
        "source": "safe-pce-clean411-baseline",
        "instance_id": case["instance_id"],
        "artifact_sha256": source.get("safe_pce_output_sha256"),
        "plan_sha256": plan_sha,
    }
    return {
        "observation_id": _stable_id(identity),
        "source_id": "safe-pce-clean411-baseline",
        "instance_id": str(case["instance_id"]),
        "outcome": "resolved" if case["resolved"] else "unresolved",
        "plan": plan,
        "plan_sha256": plan_sha,
        "historical_evidence": dict(case["asi"]),
    }


def _select_validation_tasks(
    weights: dict[str, int], *, fraction: float, seed: int
) -> set[str]:
    target = round(sum(weights.values()) * fraction)
    ordered = sorted(
        weights,
        key=lambda task: hashlib.sha256(f"{seed}:{task}".encode()).hexdigest(),
    )
    # Exact task grouping makes a target pair count a subset-sum problem. Keep
    # one deterministic path to each reachable sum, then choose the nearest.
    paths: dict[int, tuple[str, ...]] = {0: ()}
    for task in ordered:
        weight = weights[task]
        additions = {
            total + weight: chosen + (task,)
            for total, chosen in list(paths.items())
            if total + weight not in paths
        }
        paths.update(additions)
    best = min(paths, key=lambda total: (abs(total - target), total > target, total))
    return set(paths[best])


def build_snapshot(
    *,
    base_cases_path: Path,
    observations_path: Path,
    output_dir: Path,
    validation_fraction: float,
    seed: int,
    exclusions_path: Path | None = None,
    observations_manifest_path: Path | None = None,
    observation_authority: str | None = None,
    cleaning_record_path: Path | None = None,
) -> dict[str, Any]:
    if not 0 < validation_fraction < 1:
        raise ValueError("validation fraction must be between zero and one")
    cases = list(_read_jsonl(base_cases_path))
    case_by_id = {str(case["instance_id"]): case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("base cases contain duplicate task IDs")
    exclusions = {"observation_ids": [], "instance_ids": []}
    if exclusions_path is not None:
        raw = json.loads(exclusions_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not set(exclusions).issubset(raw):
            raise ValueError("exclusions must define observation_ids and instance_ids")
        exclusions = raw
    excluded_observations = set(map(str, exclusions["observation_ids"]))
    excluded_instances = set(map(str, exclusions["instance_ids"]))

    observation_index_sha = _file_sha(observations_path)
    observation_source_manifest = None
    if observations_manifest_path is not None:
        observation_source_manifest = json.loads(
            observations_manifest_path.read_text(encoding="utf-8")
        )
        if observation_source_manifest.get("output_sha256") != observation_index_sha:
            raise ValueError("observation source manifest does not bind the index")

    observations = [_baseline_observation(case) for case in cases]
    observations.extend(_read_jsonl(observations_path))
    reliability_flags: list[dict[str, Any]] = []
    patch_outcomes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for observation in observations:
        plan = str(observation.get("plan", ""))
        reasons = []
        if not plan.lstrip().startswith("# Plan"):
            reasons.append("plan_does_not_start_with_heading")
        if "</parameter>" in plan:
            reasons.append("literal_parameter_suffix")
        if len(plan.split()) < 5:
            reasons.append("plan_under_five_words")
        audit_value = observation.get("reliability_audit")
        if isinstance(audit_value, dict):
            implementation = audit_value.get("implementation_submission")
            if (
                isinstance(implementation, dict)
                and implementation.get("matches_agent_submission") is False
            ):
                reasons.append("patch_authority_mismatch")
            access = audit_value.get("source_access_summary")
            if isinstance(access, dict):
                if int(access.get("blocked_event_count", 0)):
                    reasons.append("source_access_blocked_event")
                if int(access.get("review_event_count", 0)):
                    reasons.append("source_access_manual_review")
                if int(access.get("malformed_event_count", 0)):
                    reasons.append("source_access_malformed_event")
        if reasons:
            reliability_flags.append(
                {
                    "observation_id": observation.get("observation_id"),
                    "instance_id": observation.get("instance_id"),
                    "source_id": observation.get("source_id"),
                    "reasons": reasons,
                }
            )
        patch_sha = observation.get("patch_sha256")
        if isinstance(patch_sha, str) and patch_sha:
            patch_outcomes[(str(observation["instance_id"]), patch_sha)].add(
                str(observation["outcome"])
            )
    conflicting_patches = [
        {"instance_id": task_id, "patch_sha256": patch_sha}
        for (task_id, patch_sha), outcomes in sorted(patch_outcomes.items())
        if outcomes == {"resolved", "unresolved"}
    ]
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored_outside_universe = 0
    explicitly_excluded = 0
    seen_artifacts: set[tuple[str, str]] = set()
    for observation in observations:
        task_id = str(observation["instance_id"])
        observation_id = str(observation["observation_id"])
        if task_id not in case_by_id:
            ignored_outside_universe += 1
            continue
        if task_id in excluded_instances or observation_id in excluded_observations:
            explicitly_excluded += 1
            continue
        artifact_identity = str(observation.get("artifact_sha256") or observation_id)
        dedupe_key = (task_id, artifact_identity)
        if dedupe_key in seen_artifacts:
            continue
        seen_artifacts.add(dedupe_key)
        if observation.get("outcome") not in {"resolved", "unresolved"}:
            raise ValueError(f"{observation_id}: invalid terminal outcome")
        plan = observation.get("plan")
        if not isinstance(plan, str) or not plan.strip():
            raise ValueError(f"{observation_id}: empty Plan")
        if observation.get("plan_sha256") != _sha_bytes(plan.encode("utf-8")):
            raise ValueError(f"{observation_id}: Plan hash mismatch")
        by_task[task_id].append(dict(observation))

    pairs_by_task: dict[str, list[dict[str, Any]]] = {}
    ambiguous_plan_hashes: dict[str, list[str]] = {}
    duplicate_same_outcome = 0
    for task_id, task_observations in sorted(by_task.items()):
        by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for observation in task_observations:
            by_plan[str(observation["plan_sha256"])].append(observation)
        stable: dict[str, dict[str, dict[str, Any]]] = {
            "resolved": {},
            "unresolved": {},
        }
        for plan_sha, rows in sorted(by_plan.items()):
            outcomes = {str(row["outcome"]) for row in rows}
            if len(outcomes) != 1:
                ambiguous_plan_hashes.setdefault(task_id, []).append(plan_sha)
                continue
            outcome = next(iter(outcomes))
            representative = min(rows, key=lambda row: str(row["observation_id"]))
            stable[outcome][plan_sha] = representative
            duplicate_same_outcome += len(rows) - 1
        task_pairs = []
        for resolved in stable["resolved"].values():
            for unresolved in stable["unresolved"].values():
                identity = {
                    "task_id": task_id,
                    "resolved_observation_id": resolved["observation_id"],
                    "unresolved_observation_id": unresolved["observation_id"],
                }
                pair_id = "pair-" + _stable_id(identity)[:24]
                case = case_by_id[task_id]
                repository = dict(case["checker_input"]["repository"])
                task_pairs.append(
                    {
                        "schema_version": 1,
                        "pair_id": pair_id,
                        "task_id": task_id,
                        "split": "",
                        "issue_description": case["checker_input"]["issue_description"],
                        "repository": repository,
                        "resolved_observation": {
                            key: resolved[key]
                            for key in (
                                "observation_id",
                                "plan",
                                "plan_sha256",
                                "historical_evidence",
                            )
                        },
                        "unresolved_observation": {
                            key: unresolved[key]
                            for key in (
                                "observation_id",
                                "plan",
                                "plan_sha256",
                                "historical_evidence",
                            )
                        },
                    }
                )
        if task_pairs:
            pairs_by_task[task_id] = task_pairs

    weights = {task: len(pairs) for task, pairs in pairs_by_task.items()}
    validation_tasks = _select_validation_tasks(
        weights, fraction=validation_fraction, seed=seed
    )
    train_rows = []
    validation_rows = []
    for task_id, pairs in sorted(pairs_by_task.items()):
        split = "validation" if task_id in validation_tasks else "train"
        for pair in pairs:
            row = {**pair, "split": split}
            (validation_rows if split == "validation" else train_rows).append(row)

    output_dir.mkdir(parents=True, exist_ok=False)
    for name, rows in (
        ("train.jsonl", train_rows),
        ("validation.jsonl", validation_rows),
    ):
        (output_dir / name).write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            encoding="utf-8",
        )
    reliability_flag_counts = Counter(
        reason for flag in reliability_flags for reason in flag["reasons"]
    )
    material_reasons = {
        "plan_does_not_start_with_heading",
        "literal_parameter_suffix",
        "plan_under_five_words",
        "patch_authority_mismatch",
        "source_access_malformed_event",
    }
    material_reliability_flags = [
        flag
        for flag in reliability_flags
        if material_reasons.intersection(flag["reasons"])
    ]
    audit = {
        "schema_version": 1,
        "clean_task_universe": len(case_by_id),
        "terminal_observations_after_artifact_deduplication": sum(
            map(len, by_task.values())
        ),
        "mixed_tasks": len(pairs_by_task),
        "pairs": sum(weights.values()),
        "train_tasks": len(set(pairs_by_task) - validation_tasks),
        "validation_tasks": len(validation_tasks),
        "train_pairs": len(train_rows),
        "validation_pairs": len(validation_rows),
        "ambiguous_same_plan_outcome_flip_tasks": len(ambiguous_plan_hashes),
        "ambiguous_same_plan_outcome_flip_hashes": sum(
            map(len, ambiguous_plan_hashes.values())
        ),
        "ambiguous_plan_hashes": ambiguous_plan_hashes,
        "duplicate_same_outcome_observations_collapsed": duplicate_same_outcome,
        "ignored_outside_clean_universe": ignored_outside_universe,
        "explicitly_excluded": explicitly_excluded,
        "reliability_flag_count_before_exclusions": len(reliability_flags),
        "reliability_flag_counts_before_exclusions": dict(
            sorted(reliability_flag_counts.items())
        ),
        "material_reliability_flags_before_exclusions": (material_reliability_flags),
        "identical_patch_conflicting_outcome_count_before_exclusions": len(
            conflicting_patches
        ),
        "identical_patch_conflicting_outcomes_before_exclusions": (conflicting_patches),
    }
    (output_dir / "audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if observation_source_manifest is not None:
        (output_dir / "observation_sources.json").write_text(
            json.dumps(observation_source_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    artifacts = {
        name: _file_sha(output_dir / name)
        for name in (
            "train.jsonl",
            "validation.jsonl",
            "audit.json",
            *(
                ("observation_sources.json",)
                if observation_source_manifest is not None
                else ()
            ),
        )
    }
    manifest = {
        "schema_version": 1,
        "complete": True,
        "provisional": False,
        "immutable": True,
        "data_unit": "within_task_plan_pair",
        "pair_policy": "all stable unique-plan R-by-U combinations",
        "ambiguous_plan_policy": "exclude a Plan hash observed as both R and U",
        "split_policy": "task-grouped nearest-pair-count subset-sum",
        "split_seed": seed,
        "validation_fraction": validation_fraction,
        "train_pairs": len(train_rows),
        "validation_pairs": len(validation_rows),
        "train_tasks": audit["train_tasks"],
        "validation_tasks": audit["validation_tasks"],
        "source_authorities": {
            "base_cases": str(base_cases_path),
            "base_cases_sha256": _file_sha(base_cases_path),
            "observation_index": observation_authority or str(observations_path),
            "observation_index_sha256": observation_index_sha,
            "observation_source_manifest_sha256": (
                _file_sha(observations_manifest_path)
                if observations_manifest_path is not None
                else None
            ),
            "exclusions": str(exclusions_path) if exclusions_path else None,
            "exclusions_sha256": _file_sha(exclusions_path)
            if exclusions_path
            else None,
            "cleaning_record": (
                str(cleaning_record_path) if cleaning_record_path else None
            ),
            "cleaning_record_sha256": (
                _file_sha(cleaning_record_path) if cleaning_record_path else None
            ),
        },
        "artifacts": artifacts,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"manifest": manifest, "audit": audit}


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--base-cases", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--exclusions", type=Path)
    parser.add_argument("--observations-manifest", type=Path)
    parser.add_argument("--cleaning-record", type=Path)
    parser.add_argument(
        "--observation-authority",
        help="Durable path recorded in the frozen manifest instead of the local build path",
    )
    args = parser.parse_args()
    result = build_snapshot(
        base_cases_path=args.base_cases,
        observations_path=args.observations,
        output_dir=args.output_dir,
        validation_fraction=args.validation_fraction,
        seed=args.seed,
        exclusions_path=args.exclusions,
        observations_manifest_path=args.observations_manifest,
        observation_authority=args.observation_authority,
        cleaning_record_path=args.cleaning_record,
    )
    print(json.dumps(result["audit"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
