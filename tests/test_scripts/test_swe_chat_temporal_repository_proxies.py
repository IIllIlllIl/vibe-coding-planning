from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.tools.build_swe_chat_temporal_repository_proxies import build_manifest


def git(path: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str, timestamp: int) -> str:
    target = repo / "value.txt"
    target.write_text(message, encoding="utf-8")
    git(repo, "add", "value.txt")
    env = {
        "PATH": __import__("os").environ["PATH"],
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_AUTHOR_DATE": f"@{timestamp} +0000",
        "GIT_COMMITTER_DATE": f"@{timestamp} +0000",
    }
    git(repo, "commit", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD")


def test_temporal_proxy_excludes_boundary_commit_and_backdated_descendant(tmp_path):
    working = tmp_path / "working"
    working.mkdir()
    git(working, "init", "-b", "feature")
    base = commit(working, "base", 100)
    session_commit = commit(working, "session", 200)
    # A descendant can carry a backdated committer timestamp. Timestamp filtering
    # alone must not admit it as a pre-session repository state.
    descendant = commit(working, "backdated descendant", 150)

    mirrors = tmp_path / "mirrors" / "org"
    mirrors.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--mirror", str(working), str(mirrors / "repo.git")],
        check=True,
        capture_output=True,
    )

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "session_id": "session-1",
                    "repo_id": "org/repo",
                    "checkpoint_ids": json.dumps(["org/repo#checkpoint"]),
                    "branch": "feature",
                    "created_at": datetime.fromtimestamp(200, tz=timezone.utc),
                }
            ]
        ),
        dataset / "sessions.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "checkpoint_pk": "org/repo#checkpoint",
                    "commit_sha": session_commit,
                }
            ]
        ),
        dataset / "commits.parquet",
    )
    stage2 = {
        "dataset_id": "dataset",
        "revision": "revision",
        "content_sha256": "stage2-hash",
        "cases": [
            {
                "case_id": "session-1#first-plan",
                "session_id": "session-1",
                "status": "eligible",
            }
        ],
    }
    cleaning = {
        "content_sha256": "cleaning-hash",
        "excluded_cases": [],
        "counts": {"optimization_eligible_cases": 1},
    }
    config = {
        "proxy_id": "proxy-test",
        "semantic": {"repository_state_semantics": "approximate_pre_session_proxy"},
    }

    manifest = build_manifest(
        config,
        stage2,
        cleaning,
        dataset_root=dataset,
        repositories_root=tmp_path / "mirrors",
    )

    row = manifest["cases"][0]
    assert row["proxy_commit"] == base
    assert row["proxy_commit"] not in {session_commit, descendant}
    assert row["proxy_source"] == "recorded_branch"
    assert row["time_gap_seconds"] == 100
    assert manifest["counts"]["cases"] == 1
    assert manifest["content_sha256"]


def test_temporal_proxy_falls_back_when_recorded_branch_is_missing(tmp_path):
    working = tmp_path / "working"
    working.mkdir()
    git(working, "init", "-b", "main")
    base = commit(working, "base", 100)
    git(working, "switch", "-c", "entire/checkpoints/v1")
    entire_metadata = commit(working, "metadata", 199)
    git(working, "switch", "main")
    mirrors = tmp_path / "mirrors" / "org"
    mirrors.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--mirror", str(working), str(mirrors / "repo.git")],
        check=True,
        capture_output=True,
    )

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "session_id": "session-1",
                    "repo_id": "org/repo",
                    "checkpoint_ids": "[]",
                    "branch": "deleted-feature",
                    "created_at": datetime.fromtimestamp(200, tz=timezone.utc),
                }
            ]
        ),
        dataset / "sessions.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "checkpoint_pk": pa.array([], type=pa.string()),
                "commit_sha": pa.array([], type=pa.string()),
            }
        ),
        dataset / "commits.parquet",
    )
    stage2 = {
        "dataset_id": "dataset",
        "revision": "revision",
        "content_sha256": "stage2-hash",
        "cases": [
            {
                "case_id": "session-1#first-plan",
                "session_id": "session-1",
                "status": "eligible",
            }
        ],
    }
    cleaning = {
        "content_sha256": "cleaning-hash",
        "excluded_cases": [],
        "counts": {"optimization_eligible_cases": 1},
    }
    config = {
        "proxy_id": "proxy-test",
        "semantic": {"repository_state_semantics": "approximate_pre_session_proxy"},
    }

    manifest = build_manifest(
        config,
        stage2,
        cleaning,
        dataset_root=dataset,
        repositories_root=tmp_path / "mirrors",
    )

    assert manifest["cases"][0]["proxy_commit"] == base
    assert manifest["cases"][0]["proxy_commit"] != entire_metadata
    assert manifest["cases"][0]["proxy_source"] == "all_reachable_refs"
    assert manifest["cases"][0]["recorded_branch_ref_available"] is False
