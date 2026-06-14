"""Build an immutable, cleaned Verified Round 1 dataset for GEPA.

This tool reads existing PCT artifacts only. It never runs agents, evaluators,
or external LLMs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from datasets import load_dataset


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PCT_ROOT = REPO_ROOT / "output/SWE-bench_Verified"
DEFAULT_SNAPSHOT_ROOT = (
    DEFAULT_PCT_ROOT / "verified-round1-gepa-datasets"
)
DEFAULT_SOURCE_BATCHES = (
    "verified-round1-completion-rerun",
    "verified-round1-completion",
    "run3-50-no_truncation-rerun",
    "run3-50-no_truncation",
    "run4-full-500",
    "test_runs_archive/run2",
)
DATASET_NAME = "SWE-bench/SWE-bench_Verified"

EXACT_PLACEHOLDERS = frozenset(
    {"", "test", "test content", "placeholder", "todo", "tbd", "n/a", "none"}
)
GENERIC_PLACEHOLDER_PATTERNS = (
    re.compile(r"complete the implementation as described in the pr\.?"),
)
PATH_ONLY_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(?:file|path|source file|target file|primary file(?: to modify)?)"
    r"(?:\*\*)?\s*:\s*`?/?"
    r"(?:[\w.-]+/)+[\w.-]+\."
    r"(?:py|pyi|js|ts|java|go|rs|rb|rst|md|toml|yaml|yml|json|ini|cfg)"
    r"`?\s*[.;]?\s*$",
    re.IGNORECASE,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _portable_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            )


def normalize_plan(plan: str) -> str:
    """Normalize harmless wrapper syntax for conservative placeholder checks."""
    text = plan.strip()
    text = re.sub(r"\A```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\Z", "", text)
    text = re.sub(
        r"(?im)^\s*#{1,6}\s*(?:plan|implementation plan)\s*$",
        "",
        text,
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def _semantic_lines(plan: str) -> list[str]:
    text = plan.strip()
    text = re.sub(r"\A```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\Z", "", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(
            r"#{1,6}\s*(?:plan|implementation plan|navigation(?:\s*\([a-z]\))?)",
            stripped,
            flags=re.IGNORECASE,
        ):
            continue
        lines.append(stripped)
    return lines


def placeholder_reason(plan: str) -> str | None:
    """Return a high-precision placeholder reason, or None."""
    normalized = normalize_plan(plan)
    if normalized in EXACT_PLACEHOLDERS:
        return "EXACT_PLACEHOLDER"
    if any(pattern.fullmatch(normalized) for pattern in GENERIC_PLACEHOLDER_PATTERNS):
        return "GENERIC_PLACEHOLDER"
    semantic_lines = _semantic_lines(plan)
    if (
        len(semantic_lines) == 1
        and PATH_ONLY_LINE_RE.fullmatch(semantic_lines[0])
    ):
        return "PATH_ONLY_PLAN"
    return None


def _resolve_artifact(
    instance_dir: Path,
    raw_path: str | None,
    subdirectory: str,
) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    candidates = (
        REPO_ROOT / path,
        instance_dir / path,
        instance_dir / subdirectory / path.name,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _find_code_trajectory(instance_dir: Path) -> Path | None:
    matches = sorted(
        (instance_dir / "trajectories").glob("trajectory_1_code_gen_*.json")
    )
    return matches[-1] if matches else None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _terminal_agent_failure(
    instance_id: str,
    result_path: Path,
) -> dict[str, Any] | None:
    result = _load_json(result_path)
    errors = result.get("errors")
    if result.get("plans") or not isinstance(errors, list) or not errors:
        return None
    return {
        "instance_id": instance_id,
        "reason": "terminal agent execution failure",
        "reason_code": "AGENT_EXECUTION_FAILURE",
        "source_result_path": _portable_path(result_path),
        "result_sha256": _sha256_path(result_path),
        "errors": errors,
    }


def _source_result_candidates(
    pct_root: Path,
    source_batches: Iterable[str],
) -> dict[str, list[Path]]:
    """Collect source candidates in explicit batch priority order."""
    candidates: dict[str, list[Path]] = defaultdict(list)
    for batch in source_batches:
        batch_dir = pct_root / batch
        if not batch_dir.is_dir():
            continue
        for result_path in sorted(batch_dir.glob("*/result.json")):
            candidates[result_path.parent.name].append(result_path)
    return dict(candidates)


def _load_metadata(dataset_name: str) -> dict[str, dict[str, Any]]:
    dataset = load_dataset(dataset_name, split="test")
    return {row["instance_id"]: dict(row) for row in dataset}


def _extract_case(
    instance_id: str,
    result_path: Path,
    metadata: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    result = _load_json(result_path)
    round_one = next(
        (record for record in result.get("plans", []) if record.get("round") == 1),
        None,
    )
    if round_one is None:
        return None, {"instance_id": instance_id, "reason": "missing round 1"}

    resolved = round_one.get("test_results", {}).get("resolved")
    if not isinstance(resolved, bool):
        return None, {
            "instance_id": instance_id,
            "reason": "Round 1 resolved is not a boolean",
        }

    instance_dir = result_path.parent
    plan_path = _resolve_artifact(instance_dir, round_one.get("plan_path"), "plans")
    patch_path = _resolve_artifact(
        instance_dir,
        round_one.get("generated_patch_path"),
        "patches",
    )
    plan_trajectory_path = _resolve_artifact(
        instance_dir,
        round_one.get("trajectory_path"),
        "trajectories",
    )
    code_trajectory_path = _find_code_trajectory(instance_dir)
    missing = [
        name
        for name, path in (
            ("plan", plan_path),
            ("patch", patch_path),
            ("plan trajectory", plan_trajectory_path),
            ("code trajectory", code_trajectory_path),
        )
        if path is None
    ]
    if missing:
        return None, {
            "instance_id": instance_id,
            "reason": f"missing Round 1 artifacts: {', '.join(missing)}",
        }

    assert plan_path is not None
    assert patch_path is not None
    assert plan_trajectory_path is not None
    assert code_trajectory_path is not None
    plan = plan_path.read_text(encoding="utf-8", errors="replace")
    patch = patch_path.read_text(encoding="utf-8", errors="replace")
    if not plan.strip() or not patch.strip():
        return None, {
            "instance_id": instance_id,
            "reason": "empty Round 1 plan or patch",
        }

    issue_description = metadata.get("problem_statement") or ""
    repo = metadata.get("repo") or ""
    base_commit = metadata.get("base_commit") or ""
    if not issue_description or not repo or not base_commit:
        return None, {
            "instance_id": instance_id,
            "reason": "missing official issue/repo/base_commit metadata",
        }

    plan_trajectory = _load_json(plan_trajectory_path)
    code_trajectory = _load_json(code_trajectory_path)
    test_results = round_one["test_results"]
    source_paths = {
        "result": _portable_path(result_path),
        "plan": _portable_path(plan_path),
        "patch": _portable_path(patch_path),
        "plan_trajectory": _portable_path(plan_trajectory_path),
        "code_trajectory": _portable_path(code_trajectory_path),
    }
    source_sha256 = {
        "result": _sha256_path(result_path),
        "plan": _sha256_path(plan_path),
        "patch": _sha256_path(patch_path),
        "plan_trajectory": _sha256_path(plan_trajectory_path),
        "code_trajectory": _sha256_path(code_trajectory_path),
    }
    case = {
        "schema_version": 1,
        "dataset": DATASET_NAME,
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": base_commit,
        "difficulty": metadata.get("difficulty"),
        "resolved": resolved,
        "checker_input": {
            "issue_description": issue_description,
            "plan": plan,
            "repository": {
                "repo": repo,
                "base_commit": base_commit,
                "instance_id": instance_id,
            },
        },
        "asi": {
            "plan_trajectory": plan_trajectory,
            "code_trajectory": code_trajectory,
            "generated_patch": patch,
            "evaluator_result": test_results,
        },
        "source": {
            "batch": result_path.parents[1].name,
            "run_id": result.get("run_id"),
            "model": result.get("model"),
            "parameter_n": result.get("parameter_n"),
            "paths": source_paths,
            "sha256": source_sha256,
        },
    }
    return case, None


def _assign_splits(
    cases: list[dict[str, Any]],
    *,
    validation_fraction: float,
    seed: int,
) -> None:
    strata: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        strata[(case["repo"], case["resolved"])].append(case)

    rng = random.Random(seed)
    for stratum_cases in strata.values():
        stratum_cases.sort(key=lambda case: case["instance_id"])
        rng.shuffle(stratum_cases)
        if len(stratum_cases) < 2:
            validation_count = 0
        else:
            validation_count = max(
                1, round(len(stratum_cases) * validation_fraction)
            )
            validation_count = min(validation_count, len(stratum_cases) - 1)
        validation_ids = {
            case["instance_id"] for case in stratum_cases[:validation_count]
        }
        for case in stratum_cases:
            case["split"] = (
                "validation"
                if case["instance_id"] in validation_ids
                else "train"
            )


def build_verified_gepa_input(
    *,
    pct_root: Path,
    output_dir: Path,
    metadata: dict[str, dict[str, Any]],
    source_batches: Iterable[str] = DEFAULT_SOURCE_BATCHES,
    expected_instances: int = 500,
    validation_fraction: float = 0.2,
    split_seed: int = 42,
    require_complete: bool = True,
) -> dict[str, Any]:
    """Build cleaned GEPA input files in an existing empty directory."""
    result_candidates = _source_result_candidates(pct_root, source_batches)
    selected_results: dict[str, Path] = {}
    terminal_failure_results: dict[str, Path] = {}
    cases = []
    exclusions = []
    for instance_id in sorted(metadata):
        candidates = result_candidates.get(instance_id, [])
        if not candidates:
            exclusions.append(
                {"instance_id": instance_id, "reason": "missing source result"}
            )
            continue

        case = None
        candidate_failures = []
        for result_path in candidates:
            candidate_case, exclusion = _extract_case(
                instance_id,
                result_path,
                metadata[instance_id],
            )
            if candidate_case is not None:
                case = candidate_case
                selected_results[instance_id] = result_path
                break
            assert exclusion is not None
            candidate_failures.append({
                "result_path": _portable_path(result_path),
                "reason": exclusion["reason"],
            })

        if case is None:
            terminal_failure = next(
                (
                    failure
                    for result_path in candidates
                    if (
                        failure := _terminal_agent_failure(
                            instance_id,
                            result_path,
                        )
                    )
                ),
                None,
            )
            if terminal_failure is not None:
                terminal_failure["source_failures"] = candidate_failures
                exclusions.append(terminal_failure)
                terminal_failure_results[instance_id] = next(
                    result_path
                    for result_path in candidates
                    if _portable_path(result_path)
                    == terminal_failure["source_result_path"]
                )
            else:
                exclusions.append({
                    "instance_id": instance_id,
                    "reason": "no complete Round 1 source candidate",
                    "source_failures": candidate_failures,
                })
            continue

        reason = placeholder_reason(case["checker_input"]["plan"])
        if case["resolved"] and reason:
            exclusions.append(
                {
                    "instance_id": instance_id,
                    "reason": "resolved placeholder plan",
                    "reason_code": reason,
                    "source_result_path": case["source"]["paths"]["result"],
                    "plan_sha256": case["source"]["sha256"]["plan"],
                }
            )
            continue
        cases.append(case)

    source_coverage = len(selected_results) + len(terminal_failure_results)
    source_complete = source_coverage == expected_instances
    invalid_source_exclusions = [
        exclusion
        for exclusion in exclusions
        if exclusion.get("reason_code")
        not in {
            "AGENT_EXECUTION_FAILURE",
            "EXACT_PLACEHOLDER",
            "GENERIC_PLACEHOLDER",
            "PATH_ONLY_PLAN",
        }
    ]
    artifact_complete = (
        len(cases) + len(exclusions) == expected_instances
        and not invalid_source_exclusions
    )
    if require_complete and (not source_complete or not artifact_complete):
        raise ValueError(
            "Verified Round 1 source is incomplete: "
            f"source_results={source_coverage}/{expected_instances}, "
            f"invalid_or_missing={len(invalid_source_exclusions)}"
        )

    _assign_splits(
        cases,
        validation_fraction=validation_fraction,
        seed=split_seed,
    )
    cases.sort(key=lambda case: case["instance_id"])
    output_dir.mkdir(parents=True, exist_ok=True)
    cases_path = output_dir / "cases.jsonl"
    train_path = output_dir / "train.jsonl"
    validation_path = output_dir / "validation.jsonl"
    exclusions_path = output_dir / "exclusions.json"
    _write_jsonl(cases_path, cases)
    _write_jsonl(
        train_path, (case for case in cases if case["split"] == "train")
    )
    _write_jsonl(
        validation_path,
        (case for case in cases if case["split"] == "validation"),
    )
    _write_json(exclusions_path, exclusions)

    train_cases = [case for case in cases if case["split"] == "train"]
    validation_cases = [
        case for case in cases if case["split"] == "validation"
    ]
    placeholder_exclusions = [
        exclusion
        for exclusion in exclusions
        if exclusion.get("reason_code")
        in {"EXACT_PLACEHOLDER", "GENERIC_PLACEHOLDER", "PATH_ONLY_PLAN"}
    ]
    source_exclusions = [
        exclusion
        for exclusion in exclusions
        if exclusion.get("reason_code") == "AGENT_EXECUTION_FAILURE"
    ]
    manifest = {
        "schema_version": 1,
        "dataset": DATASET_NAME,
        "selection_policy": "explicit-batch-priority-round-1-v1",
        "source_batches": list(source_batches),
        "cleaning_policy": "resolved-placeholder-high-precision-v1",
        "source_exclusion_policy": "terminal-agent-execution-failure-v1",
        "expected_instances": expected_instances,
        "source_results": source_coverage,
        "complete": source_complete and artifact_complete,
        "provisional": not (source_complete and artifact_complete),
        "invalid_source_instances": len(invalid_source_exclusions),
        "agent_execution_failure_instances": len(terminal_failure_results),
        "placeholder_exclusion_instances": len(placeholder_exclusions),
        "source_exclusion_instances": len(source_exclusions),
        "selected_instances": len(cases),
        "excluded_instances": len(exclusions),
        "resolved": sum(case["resolved"] for case in cases),
        "unresolved": sum(not case["resolved"] for case in cases),
        "train_instances": len(train_cases),
        "validation_instances": len(validation_cases),
        "train_resolved": sum(case["resolved"] for case in train_cases),
        "validation_resolved": sum(
            case["resolved"] for case in validation_cases
        ),
        "split_policy": "repo-and-resolved-stratified-v1",
        "split_seed": split_seed,
        "validation_fraction": validation_fraction,
        "exclusion_reason_counts": dict(
            sorted(Counter(
                exclusion.get("reason_code", exclusion["reason"])
                for exclusion in exclusions
            ).items())
        ),
        "cleaning_exclusion_reason_counts": dict(
            sorted(Counter(
                exclusion["reason_code"]
                for exclusion in placeholder_exclusions
            ).items())
        ),
        "source_exclusion_reason_counts": dict(
            sorted(Counter(
                exclusion["reason_code"]
                for exclusion in source_exclusions
            ).items())
        ),
        "cases_sha256": _sha256_path(cases_path),
        "train_sha256": _sha256_path(train_path),
        "validation_sha256": _sha256_path(validation_path),
        "exclusions_sha256": _sha256_path(exclusions_path),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def publish_verified_gepa_snapshot(
    *,
    pct_root: Path,
    snapshot_root: Path,
    metadata: dict[str, dict[str, Any]],
    source_batches: Iterable[str] = DEFAULT_SOURCE_BATCHES,
    expected_instances: int = 500,
    validation_fraction: float = 0.2,
    split_seed: int = 42,
    require_complete: bool = True,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Publish or reuse an immutable content-addressed GEPA dataset snapshot."""
    snapshot_root.mkdir(parents=True, exist_ok=True)
    build_dir = Path(
        tempfile.mkdtemp(prefix=".building-", dir=str(snapshot_root))
    )
    try:
        manifest = build_verified_gepa_input(
            pct_root=pct_root,
            output_dir=build_dir,
            metadata=metadata,
            source_batches=source_batches,
            expected_instances=expected_instances,
            validation_fraction=validation_fraction,
            split_seed=split_seed,
            require_complete=require_complete,
        )
        index_path = snapshot_root / "index.json"
        index = {"schema_version": 1, "snapshots": []}
        if index_path.is_file():
            loaded = _load_json(index_path)
            if isinstance(loaded, dict):
                index = loaded
                index.setdefault("snapshots", [])

        existing = None
        publication_identity_keys = (
            "cases_sha256",
            "train_sha256",
            "validation_sha256",
            "exclusions_sha256",
            "cleaning_policy",
            "source_exclusion_policy",
        )
        for entry in index["snapshots"]:
            snapshot_dir = snapshot_root / entry["snapshot_id"]
            snapshot_manifest_path = snapshot_dir / "manifest.json"
            if not snapshot_manifest_path.is_file():
                continue
            snapshot_manifest = _load_json(snapshot_manifest_path)
            if all(
                snapshot_manifest.get(key) == manifest[key]
                for key in publication_identity_keys
            ):
                existing = entry
                break
        if existing:
            shutil.rmtree(build_dir)
            snapshot_dir = snapshot_root / existing["snapshot_id"]
            manifest = _load_json(snapshot_dir / "manifest.json")
        else:
            timestamp = created_at or datetime.now(timezone.utc)
            publication_hash = _sha256_bytes(json.dumps(
                {
                    key: manifest[key]
                    for key in (
                        "cases_sha256",
                        "train_sha256",
                        "validation_sha256",
                        "exclusions_sha256",
                        "cleaning_policy",
                        "source_exclusion_policy",
                    )
                },
                sort_keys=True,
            ).encode("utf-8"))
            snapshot_id = (
                f"{timestamp:%Y%m%d}_{manifest['selected_instances']}_"
                f"{publication_hash[:12]}"
            )
            snapshot_dir = snapshot_root / snapshot_id
            if snapshot_dir.exists():
                raise ValueError(f"snapshot ID collision: {snapshot_dir}")
            manifest.update({
                "snapshot_id": snapshot_id,
                "snapshot_path": _portable_path(snapshot_dir),
                "cases_path": _portable_path(snapshot_dir / "cases.jsonl"),
                "train_path": _portable_path(snapshot_dir / "train.jsonl"),
                "validation_path": _portable_path(
                    snapshot_dir / "validation.jsonl"
                ),
                "exclusions_path": _portable_path(
                    snapshot_dir / "exclusions.json"
                ),
                "created_at": timestamp.isoformat(),
                "immutable": True,
            })
            _write_json(build_dir / "manifest.json", manifest)
            build_dir.rename(snapshot_dir)

        entry = {
            key: manifest[key]
            for key in (
                "snapshot_id",
                "snapshot_path",
                "created_at",
                "cases_sha256",
                "train_sha256",
                "validation_sha256",
                "exclusions_sha256",
                "selected_instances",
                "excluded_instances",
                "resolved",
                "unresolved",
                "train_instances",
                "validation_instances",
                "complete",
                "provisional",
                "invalid_source_instances",
                "agent_execution_failure_instances",
            )
        }
        snapshots = [
            item for item in index["snapshots"]
            if item.get("snapshot_id") != entry["snapshot_id"]
        ]
        snapshots.append(entry)
        snapshots.sort(key=lambda item: (item["created_at"], item["snapshot_id"]))
        index.update({
            "schema_version": 1,
            "latest_snapshot_id": manifest["snapshot_id"],
            "latest_cases_path": manifest["cases_path"],
            "latest_train_path": manifest["train_path"],
            "latest_validation_path": manifest["validation_path"],
            "snapshots": snapshots,
        })
        _write_json(index_path, index)
        return manifest
    finally:
        if build_dir.exists():
            shutil.rmtree(build_dir)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pct-root", type=Path, default=DEFAULT_PCT_ROOT)
    parser.add_argument(
        "--snapshot-root",
        type=Path,
        default=DEFAULT_SNAPSHOT_ROOT,
    )
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Publish a provisional snapshot even when source artifacts are missing.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    metadata = _load_metadata(args.dataset)
    manifest = publish_verified_gepa_snapshot(
        pct_root=args.pct_root,
        snapshot_root=args.snapshot_root,
        metadata=metadata,
        expected_instances=len(metadata),
        validation_fraction=args.validation_fraction,
        split_seed=args.split_seed,
        require_complete=not args.allow_incomplete,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
