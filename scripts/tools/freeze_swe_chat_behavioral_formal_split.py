"""Freeze the repository-disjoint Behavioral v1 formal train/validation split."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import Any


NEAR_DUPLICATE_THRESHOLD = 0.90
SHINGLE_SIZE = 5


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_sha256(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_sha256", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def _shingles(text: str) -> frozenset[tuple[str, ...]]:
    tokens = re.findall(r"[\w./:+-]+", _normalize(text), flags=re.UNICODE)
    if len(tokens) < SHINGLE_SIZE:
        return frozenset({tuple(tokens)}) if tokens else frozenset()
    return frozenset(
        tuple(tokens[index : index + SHINGLE_SIZE])
        for index in range(len(tokens) - SHINGLE_SIZE + 1)
    )


def _jaccard(left: frozenset[Any], right: frozenset[Any]) -> float:
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


class _Groups:
    def __init__(self, values: set[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        self.parent[second] = first


def freeze_split(
    *,
    stage2_manifest_path: Path,
    stage2_case_root: Path,
    repository_cleaning_path: Path,
    proxy_manifest_path: Path,
    development_split_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(f"split output already exists: {output_path}")
    stage2 = _load(stage2_manifest_path)
    cleaning = _load(repository_cleaning_path)
    proxies = _load(proxy_manifest_path)
    development = _load(development_split_path)
    if development.get("content_sha256") != _content_sha256(development):
        raise ValueError("development split content hash mismatch")

    excluded = {item["case_id"] for item in cleaning["excluded_cases"]}
    eligible = {
        item["case_id"]: item
        for item in stage2["cases"]
        if item["status"] == "eligible" and item["case_id"] not in excluded
    }
    proxy_by_id = {item["case_id"]: item for item in proxies["cases"]}
    if set(proxy_by_id) != set(eligible):
        raise ValueError("proxy cases do not match repository-ready cases")
    development_ids = {
        item["case_id"] for item in development.get("assignments", [])
    }
    if not development_ids or not development_ids <= set(eligible):
        raise ValueError("development cases must be repository-ready")

    records: dict[str, dict[str, Any]] = {}
    for case_id in sorted(eligible):
        entry = eligible[case_id]
        case_path = stage2_case_root / entry["case_path"]
        if _file_sha256(case_path) != entry["case_sha256"]:
            raise ValueError(f"{case_id}: Stage-2 case hash mismatch")
        source = _load(case_path)
        if source.get("case_id") != case_id:
            raise ValueError(f"{case_id}: Stage-2 case identity mismatch")
        repo_id = str(source["selection_provenance"]["repo_id"])
        proxy = proxy_by_id[case_id]
        if proxy["repo_id"] != repo_id:
            raise ValueError(f"{case_id}: repository identity mismatch")
        plan = str(source["checker_visible"]["proposed_plan"])
        if hashlib.sha256(plan.encode("utf-8")).hexdigest() != source["boundary"][
            "proposed_plan_sha256"
        ]:
            raise ValueError(f"{case_id}: proposed Plan hash mismatch")
        user_context = "\n".join(
            str(event.get("content", ""))
            for event in source["checker_visible"]["events"]
            if event.get("role") == "user" and event.get("turn_type") == "user_prompt"
        )
        signal = source["reflection_only"]["behavior_signal"]
        if signal not in {"explicit_approval", "explicit_rejection"}:
            raise ValueError(f"{case_id}: unsupported behavioral signal")
        records[case_id] = {
            "repo_id": repo_id,
            "signal": signal,
            "proxy_source": proxy["proxy_source"],
            "normalized_plan_sha256": hashlib.sha256(
                _normalize(plan).encode("utf-8")
            ).hexdigest(),
            "normalized_user_context_sha256": hashlib.sha256(
                _normalize(user_context).encode("utf-8")
            ).hexdigest(),
            "plan_shingles": _shingles(plan),
        }

    repositories = {record["repo_id"] for record in records.values()}
    groups = _Groups(repositories)
    exact_plan_pairs = 0
    exact_context_pairs = 0
    near_plan_pairs = 0
    case_ids = sorted(records)
    for index, left_id in enumerate(case_ids):
        left = records[left_id]
        for right_id in case_ids[index + 1 :]:
            right = records[right_id]
            if left["repo_id"] == right["repo_id"]:
                continue
            duplicate = False
            if left["normalized_plan_sha256"] == right["normalized_plan_sha256"]:
                exact_plan_pairs += 1
                duplicate = True
            if (
                left["normalized_user_context_sha256"]
                == right["normalized_user_context_sha256"]
            ):
                exact_context_pairs += 1
                duplicate = True
            if _jaccard(left["plan_shingles"], right["plan_shingles"]) >= (
                NEAR_DUPLICATE_THRESHOLD
            ):
                near_plan_pairs += 1
                duplicate = True
            if duplicate:
                groups.union(left["repo_id"], right["repo_id"])

    components: dict[str, set[str]] = {}
    for repo_id in sorted(repositories):
        components.setdefault(groups.find(repo_id), set()).add(repo_id)
    development_repositories = {
        records[case_id]["repo_id"] for case_id in development_ids
    }
    train_components = {
        root
        for root, repos in components.items()
        if repos & development_repositories
    }
    split_by_repo = {
        repo_id: ("train" if groups.find(repo_id) in train_components else "validation")
        for repo_id in repositories
    }

    assignments = []
    counts: dict[str, Counter[str]] = {
        "train": Counter(),
        "validation": Counter(),
    }
    for case_id in case_ids:
        record = records[case_id]
        split = split_by_repo[record["repo_id"]]
        component_repos = sorted(components[groups.find(record["repo_id"])])
        component_hash = hashlib.sha256(
            "\n".join(component_repos).encode("utf-8")
        ).hexdigest()[:16]
        assignments.append(
            {
                "case_id": case_id,
                "split": split,
                "dedup_group": f"repository-component:{component_hash}",
            }
        )
        counts[split]["cases"] += 1
        counts[split][
            "ACCEPT" if record["signal"] == "explicit_approval" else "DO_NOT_ACCEPT"
        ] += 1
        counts[split][record["proxy_source"]] += 1

    if any(split_by_repo[records[case_id]["repo_id"]] != "train" for case_id in development_ids):
        raise AssertionError("every development-exposed case must enter formal train")
    train_repos = {repo for repo, split in split_by_repo.items() if split == "train"}
    validation_repos = repositories - train_repos
    if train_repos & validation_repos:
        raise AssertionError("repository split overlap")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "split_id": "swe-chat-behavioral-formal-repository-holdout-v1-20260830",
        "purpose": "formal_behavioral_gepa_8_iteration_optimization",
        "complete": True,
        "provisional": False,
        "case_universe": "all_repository_ready",
        "source_bindings": {
            "stage2_manifest_content_sha256": stage2["content_sha256"],
            "stage2_manifest_byte_sha256": _file_sha256(stage2_manifest_path),
            "repository_cleaning_content_sha256": cleaning["content_sha256"],
            "repository_cleaning_byte_sha256": _file_sha256(
                repository_cleaning_path
            ),
            "temporal_proxy_content_sha256": proxies["content_sha256"],
            "temporal_proxy_byte_sha256": _file_sha256(proxy_manifest_path),
            "development_split_content_sha256": development["content_sha256"],
            "development_split_byte_sha256": _file_sha256(development_split_path),
        },
        "selection_policy": {
            "label_independent_assignment": True,
            "train": (
                "every repository component containing a Stage-B/C development "
                "case"
            ),
            "validation": "every remaining repository component",
            "development_exposed_cases_may_enter_formal_train_only": True,
            "repository_disjoint": True,
            "session_and_conversation_disjoint": True,
            "exact_duplicate_keys": [
                "normalized_proposed_plan_sha256",
                "normalized_pre_p1_user_context_sha256",
            ],
            "near_duplicate_algorithm": (
                "NFKC-lowercase-whitespace-normalized proposed Plan; Unicode-word "
                f"{SHINGLE_SIZE}-shingle Jaccard >= {NEAR_DUPLICATE_THRESHOLD:.2f}"
            ),
            "cross_repository_duplicate_components_are_atomic": True,
            "formal_validation_is_candidate_selection_data_not_untouched_holdout": True,
        },
        "counts": {
            split: dict(sorted(value.items())) for split, value in counts.items()
        },
        "repository_counts": {
            "total": len(repositories),
            "train": len(train_repos),
            "validation": len(validation_repos),
        },
        "duplicate_audit": {
            "cross_repository_exact_plan_pairs": exact_plan_pairs,
            "cross_repository_exact_user_context_pairs": exact_context_pairs,
            "cross_repository_near_plan_pairs": near_plan_pairs,
            "repository_components": len(components),
            "cross_split_repository_or_duplicate_components": 0,
        },
        "development_exposed_case_ids": sorted(development_ids),
        "assignments": assignments,
    }
    manifest["content_sha256"] = _content_sha256(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--stage2-manifest", required=True, type=Path)
    parser.add_argument("--stage2-case-root", required=True, type=Path)
    parser.add_argument("--repository-cleaning", required=True, type=Path)
    parser.add_argument("--proxy-manifest", required=True, type=Path)
    parser.add_argument("--development-split", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    freeze_split(
        stage2_manifest_path=args.stage2_manifest,
        stage2_case_root=args.stage2_case_root,
        repository_cleaning_path=args.repository_cleaning,
        proxy_manifest_path=args.proxy_manifest,
        development_split_path=args.development_split,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
