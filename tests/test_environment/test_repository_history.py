from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from src.environment.repository_history import (
    REPOSITORY_HISTORY_POLICY,
    RepositoryHistoryCache,
    install_repository_history_bundle,
    repository_history_key,
)


class LocalEnvironment:
    def __init__(self, repository: Path) -> None:
        self.repository = repository

    def execute(self, command: str, timeout: int | None = None) -> dict:
        result = subprocess.run(
            ["bash", "-lc", command],
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "returncode": result.returncode,
            "output": result.stdout + result.stderr,
        }


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(repository: Path, date: str, message: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
    }
    subprocess.run(
        ["git", "commit", "-qam", message],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return _git(repository, "rev-parse", "HEAD")


def test_cached_bundle_exposes_base_ancestors_but_not_future_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.invalid")
    _git(source, "config", "user.name", "Test")
    (source / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (source / "tracked.txt").write_text("ancestor\n", encoding="utf-8")
    _git(source, "add", ".gitignore", "tracked.txt")
    ancestor = _commit(source, "2000-01-01T00:00:00Z", "ancestor")
    (source / "tracked.txt").write_text("base\n", encoding="utf-8")
    base = _commit(source, "2000-01-02T00:00:00Z", "base")
    (source / "tracked.txt").write_text("future\n", encoding="utf-8")
    future = _commit(source, "2000-01-03T00:00:00Z", "future")
    (source / "ignored.txt").write_text("image setup\n", encoding="utf-8")

    # The production Aion worker starts outside a Git repository.  Keeping
    # this cwd outside ``source`` catches accidental reliance on the host
    # process being inside some unrelated repository.
    monkeypatch.chdir(tmp_path)
    cache = RepositoryHistoryCache(tmp_path / "cache")
    bundle, manifest = cache.ensure(
        env=LocalEnvironment(source),
        repository_dir=source,
        sif_sha256="a" * 64,
        base_commit=base,
        instance_id="repo__repo-1",
        timeout=30,
    )

    assert manifest["policy"] == REPOSITORY_HISTORY_POLICY
    assert manifest["pack_limits"]["threads"] == 1
    assert cache.validate(sif_sha256="a" * 64, base_commit=base) is not None
    assert _git(source, "rev-parse", "HEAD") == future
    assert (source / "tracked.txt").read_text(encoding="utf-8") == "future\n"

    target = tmp_path / "target"
    shutil.copytree(source, target)
    evidence = install_repository_history_bundle(
        repository_dir=target,
        bundle=bundle,
        base_commit=base,
    )

    assert evidence["policy"] == REPOSITORY_HISTORY_POLICY
    assert _git(target, "rev-parse", "HEAD") == base
    assert _git(target, "cat-file", "-t", ancestor) == "commit"
    missing = subprocess.run(
        ["git", "cat-file", "-e", f"{future}^{{commit}}"],
        cwd=target,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert (target / "ignored.txt").read_text(encoding="utf-8") == "image setup\n"
    assert _git(target, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_cache_key_binds_policy_sif_and_base() -> None:
    first = repository_history_key(sif_sha256="a" * 64, base_commit="b" * 40)
    assert first == repository_history_key(
        sif_sha256="a" * 64,
        base_commit="b" * 40,
    )
    assert first != repository_history_key(
        sif_sha256="c" * 64,
        base_commit="b" * 40,
    )
    assert first != repository_history_key(
        sif_sha256="a" * 64,
        base_commit="d" * 40,
    )
