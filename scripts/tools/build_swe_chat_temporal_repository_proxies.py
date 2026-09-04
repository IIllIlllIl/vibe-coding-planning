#!/usr/bin/env python3
"""Build deterministic pre-session repository proxies for SWE-chat cases."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable

import pyarrow.parquet as pq
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def content_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_frozen_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("content_sha256") != content_sha256(value):
        raise ValueError(f"{path}: content_sha256 mismatch")
    return value


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def git(mirror: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--git-dir", str(mirror), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )


def mirror_path(repositories_root: Path, repo_id: str) -> Path:
    owner, name = repo_id.split("/", 1)
    return repositories_root / owner / f"{name}.git"


def parse_checkpoint_ids(value: Any) -> list[str]:
    parsed = json.loads(value or "[]")
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) for item in parsed
    ):
        raise ValueError("sessions.checkpoint_ids must be a JSON string array")
    return parsed


def source_refs(mirror: Path) -> list[str]:
    result = git(
        mirror,
        "for-each-ref",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
        "refs/pull",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to enumerate Git refs for {mirror}: {result.stderr}"
        )
    refs = []
    for ref in result.stdout.splitlines():
        short = ref.removeprefix("refs/heads/")
        if short.startswith("entire/") or "/entire/" in ref:
            continue
        refs.append(ref)
    if not refs:
        raise ValueError(f"no source refs remain after Entire ref exclusion: {mirror}")
    return sorted(refs)


def reachable_history(mirror: Path, refs: str | Iterable[str]) -> list[tuple[int, str]]:
    arguments = ["rev-list", "--timestamp"]
    arguments.extend([refs] if isinstance(refs, str) else refs)
    result = git(mirror, *arguments)
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to enumerate Git history for {mirror}: {result.stderr}"
        )
    by_commit: dict[str, int] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        timestamp, commit = int(fields[0]), fields[1]
        by_commit[commit] = max(timestamp, by_commit.get(commit, timestamp))
    return sorted(
        ((timestamp, commit) for commit, timestamp in by_commit.items()),
        key=lambda item: (-item[0], item[1]),
    )


def existing_recorded_ref(mirror: Path, branch: str) -> str | None:
    if not branch:
        return None
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        if git(mirror, "show-ref", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def commit_exists(mirror: Path, commit: str) -> bool:
    return git(mirror, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def is_ancestor(mirror: Path, ancestor: str, descendant: str) -> bool:
    return (
        git(mirror, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0
    )


def select_proxy(
    mirror: Path,
    history: Iterable[tuple[int, str]],
    *,
    session_second: int,
    session_commits: set[str],
) -> tuple[int, str] | None:
    present_session_commits = {
        commit for commit in session_commits if commit_exists(mirror, commit)
    }
    for timestamp, commit in history:
        # Git timestamps have one-second precision. Excluding the entire session
        # creation second avoids admitting a checkpoint commit stamped at the
        # boundary while the Parquet timestamp retains sub-second precision.
        if timestamp >= session_second or commit in session_commits:
            continue
        if any(
            is_ancestor(mirror, session_commit, commit)
            for session_commit in present_session_commits
        ):
            continue
        return timestamp, commit
    return None


def build_manifest(
    config: dict[str, Any],
    stage2: dict[str, Any],
    cleaning: dict[str, Any],
    *,
    dataset_root: Path,
    repositories_root: Path,
) -> dict[str, Any]:
    excluded = {item["case_id"] for item in cleaning["excluded_cases"]}
    eligible = {
        item["session_id"]: item["case_id"]
        for item in stage2["cases"]
        if item["status"] == "eligible" and item["case_id"] not in excluded
    }
    if len(eligible) != cleaning["counts"]["optimization_eligible_cases"]:
        raise ValueError("repository-ready case universe has wrong size")

    sessions = pq.read_table(
        dataset_root / "sessions.parquet",
        columns=[
            "session_id",
            "repo_id",
            "checkpoint_ids",
            "branch",
            "created_at",
        ],
        filters=[("session_id", "in", sorted(eligible))],
    ).to_pylist()
    if {row["session_id"] for row in sessions} != set(eligible):
        raise ValueError("sessions.parquet does not contain the complete case universe")

    checkpoint_ids = {
        checkpoint
        for row in sessions
        for checkpoint in parse_checkpoint_ids(row["checkpoint_ids"])
    }
    commits = (
        pq.read_table(
            dataset_root / "commits.parquet",
            columns=["checkpoint_pk", "commit_sha"],
            filters=[("checkpoint_pk", "in", sorted(checkpoint_ids))],
        ).to_pylist()
        if checkpoint_ids
        else []
    )
    commits_by_checkpoint: dict[str, set[str]] = defaultdict(set)
    for row in commits:
        if isinstance(row["commit_sha"], str) and row["commit_sha"]:
            commits_by_checkpoint[row["checkpoint_pk"]].add(row["commit_sha"])

    all_history: dict[str, list[tuple[int, str]]] = {}
    records = []
    for session in sorted(sessions, key=lambda row: row["session_id"]):
        repo_id = session["repo_id"]
        mirror = mirror_path(repositories_root, repo_id)
        if not mirror.is_dir():
            raise ValueError(f"repository mirror missing for {repo_id}: {mirror}")
        if repo_id not in all_history:
            all_history[repo_id] = reachable_history(mirror, source_refs(mirror))

        created_at = session["created_at"]
        if not isinstance(created_at, datetime):
            raise ValueError(f"{session['session_id']}: created_at is not a timestamp")
        session_second = int(created_at.timestamp())
        session_commits = {
            commit
            for checkpoint in parse_checkpoint_ids(session["checkpoint_ids"])
            for commit in commits_by_checkpoint.get(checkpoint, set())
        }

        recorded_ref = existing_recorded_ref(mirror, str(session["branch"] or ""))
        selected = None
        source = None
        if recorded_ref is not None:
            selected = select_proxy(
                mirror,
                reachable_history(mirror, recorded_ref),
                session_second=session_second,
                session_commits=session_commits,
            )
            if selected is not None:
                source = "recorded_branch"
        if selected is None:
            selected = select_proxy(
                mirror,
                all_history[repo_id],
                session_second=session_second,
                session_commits=session_commits,
            )
            source = "all_reachable_refs"
        if selected is None:
            raise ValueError(f"{session['session_id']}: no safe pre-session commit")

        commit_timestamp, commit = selected
        tree = git(mirror, "rev-parse", f"{commit}^{{tree}}")
        if tree.returncode != 0:
            raise ValueError(f"{session['session_id']}: proxy commit has no tree")
        records.append(
            {
                "case_id": eligible[session["session_id"]],
                "session_id": session["session_id"],
                "repo_id": repo_id,
                "proxy_commit": commit,
                "proxy_tree": tree.stdout.strip(),
                "proxy_source": source,
                "recorded_branch": session["branch"],
                "recorded_branch_ref_available": recorded_ref is not None,
                "session_created_at": created_at.isoformat(),
                "proxy_commit_timestamp": commit_timestamp,
                "time_gap_seconds": session_second - commit_timestamp,
                "session_checkpoint_commit_count": len(session_commits),
                "session_checkpoint_commits_present_in_mirror": sum(
                    commit_exists(mirror, item) for item in session_commits
                ),
                "repository_state_semantics": "approximate_pre_session_proxy",
            }
        )

    gaps = sorted(record["time_gap_seconds"] for record in records)

    def percentile(fraction: float) -> int:
        return gaps[int((len(gaps) - 1) * fraction)]

    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_temporal_repository_proxy_manifest",
        "proxy_id": config["proxy_id"],
        "dataset_id": stage2["dataset_id"],
        "revision": stage2["revision"],
        "stage2_manifest_sha256": stage2["content_sha256"],
        "repository_cleaning_manifest_sha256": cleaning["content_sha256"],
        "semantic_policy": config["semantic"],
        "counts": {
            "cases": len(records),
            "repositories": len({record["repo_id"] for record in records}),
            "proxy_sources": dict(
                sorted(Counter(record["proxy_source"] for record in records).items())
            ),
            "recorded_branch_ref_available": sum(
                record["recorded_branch_ref_available"] for record in records
            ),
        },
        "time_gap_seconds": {
            "min": gaps[0],
            "p50": percentile(0.5),
            "p90": percentile(0.9),
            "p95": percentile(0.95),
            "max": gaps[-1],
        },
        "cases": records,
    }
    manifest["content_sha256"] = content_sha256(manifest)
    return manifest


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--stage2-manifest", type=Path)
    parser.add_argument("--cleaning-manifest", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--repositories-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        config.get("schema_version") != 1
        or config.get("purpose") != "swe_chat_temporal_repository_proxy"
    ):
        raise ValueError("temporal repository proxy config has wrong schema")
    semantic = config["semantic"]
    stage2_path = args.stage2_manifest or resolve_path(semantic["stage2_manifest"])
    cleaning_path = args.cleaning_manifest or resolve_path(
        semantic["repository_cleaning_manifest"]
    )
    stage2 = load_frozen_json(stage2_path)
    cleaning = load_frozen_json(cleaning_path)
    if stage2["content_sha256"] != semantic["stage2_manifest_sha256"]:
        raise ValueError("configured Stage-2 manifest hash does not match")
    if cleaning["content_sha256"] != semantic["repository_cleaning_manifest_sha256"]:
        raise ValueError("configured repository-cleaning manifest hash does not match")
    manifest = build_manifest(
        config,
        stage2,
        cleaning,
        dataset_root=args.dataset_root or Path(config["operational"]["dataset_root"]),
        repositories_root=args.repositories_root
        or Path(config["operational"]["repositories_root"]),
    )
    manifest["builder_sha256"] = file_sha256(Path(__file__).resolve())
    manifest["config_sha256"] = file_sha256(config_path)
    manifest["content_sha256"] = content_sha256(manifest)
    atomic_json(args.output, manifest)


if __name__ == "__main__":
    main()
