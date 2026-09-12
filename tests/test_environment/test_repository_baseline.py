from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from src.environment.repository_baseline import restore_repository_to_base
from src.exceptions import FatalError


class FakeEnvironment:
    def __init__(self, *, base_present: bool = True, clean_after: bool = True):
        self.base_present = base_present
        self.clean_after = clean_after
        self.restored = False
        self.commands: list[str] = []

    def execute(self, command: str, timeout: int | None = None) -> dict:
        self.commands.append(command)
        if command.startswith("git cat-file"):
            return {"returncode": 0 if self.base_present else 1, "output": ""}
        if command.startswith("git reset --hard"):
            self.restored = True
            return {"returncode": 0, "output": "HEAD is now at abc"}
        if command.startswith("git show -s --format=%ct"):
            return {"returncode": 0, "output": "100\n"}
        if command.startswith("TARGET_EPOCH="):
            return {"returncode": 0, "output": ""}
        if command.startswith("git log --all --format="):
            return {"returncode": 0, "output": "abc 100\n"}
        if command == "git rev-parse HEAD":
            return {"returncode": 0, "output": "abc\n"}
        if command.startswith("git status"):
            output = "" if self.restored and self.clean_after else "?? dirty.txt\n"
            return {"returncode": 0, "output": output}
        if command.startswith("git diff"):
            return {"returncode": 0, "output": "dirty diff"}
        raise AssertionError(command)


def test_restore_records_before_state_and_verifies_clean_base(tmp_path) -> None:
    env = FakeEnvironment()
    evidence = restore_repository_to_base(
        env, "abc", phase="code", evidence_dir=tmp_path
    )

    assert evidence["before"]["status"]["output"] == "?? dirty.txt\n"
    assert evidence["after"]["status"]["output"] == ""
    assert "git reset --hard abc && git clean -fd" in env.commands
    saved = json.loads((tmp_path / "repository_baseline.json").read_text())
    assert saved["declared_base_commit"] == "abc"


def test_restore_blocks_when_declared_commit_is_missing(tmp_path) -> None:
    with pytest.raises(FatalError, match="does not contain"):
        restore_repository_to_base(
            FakeEnvironment(base_present=False),
            "abc",
            phase="plan",
            evidence_dir=tmp_path,
        )
    assert (tmp_path / "repository_baseline.json").is_file()


def test_restore_blocks_when_worktree_remains_dirty(tmp_path) -> None:
    with pytest.raises(FatalError, match="not clean"):
        restore_repository_to_base(
            FakeEnvironment(clean_after=False),
            "abc",
            phase="checker",
            evidence_dir=tmp_path,
        )


def test_restore_can_prune_future_history(tmp_path) -> None:
    env = FakeEnvironment()

    evidence = restore_repository_to_base(
        env,
        "abc",
        phase="plan",
        evidence_dir=tmp_path,
        prune_future_history=True,
    )

    assert evidence["future_history_prune"]["returncode"] == 0
    assert evidence["base_commit_epoch"]["output"] == "100\n"
    assert evidence["future_history_check"]["output"] == "abc 100\n"
    assert any("git remote remove" in command for command in env.commands)
    assert any("TAG_EPOCH" in command for command in env.commands)
    assert any("git reflog expire" in command for command in env.commands)
    assert any("git gc --prune=now --aggressive" in command for command in env.commands)
    assert all("while read" not in command for command in env.commands)


def test_future_commit_is_unavailable_after_real_repository_prune(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def commit_at(date: str, *args: str) -> None:
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **env},
        )

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repository / "history.txt").write_text("ancestor\n", encoding="utf-8")
    git("add", "history.txt")
    commit_at("2000-01-01T00:00:00Z", "commit", "-qm", "ancestor")
    ancestor = git("rev-parse", "HEAD")
    git("tag", "old-tag")
    (repository / "value.txt").write_text("base\n", encoding="utf-8")
    git("add", "value.txt")
    commit_at("2000-01-02T00:00:00Z", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repository / "value.txt").write_text("future\n", encoding="utf-8")
    commit_at("2000-01-03T00:00:00Z", "commit", "-qam", "future")
    future = git("rev-parse", "HEAD")
    git("tag", "future-tag")
    git("branch", "future-branch")
    git("remote", "add", "origin", "https://example.invalid/repo.git")
    git("update-ref", "refs/remotes/origin/future", future)

    class LocalGitEnvironment:
        def execute(self, command: str, timeout: int | None = None) -> dict:
            result = subprocess.run(
                ["bash", "-lc", command],
                cwd=repository,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            return {
                "returncode": result.returncode,
                "output": result.stdout + result.stderr,
            }

    restore_repository_to_base(
        LocalGitEnvironment(),
        base,
        phase="plan",
        evidence_dir=tmp_path / "evidence",
        prune_future_history=True,
    )

    assert git("rev-parse", "HEAD") == base
    assert git("merge-base", "--is-ancestor", ancestor, base) == ""
    assert git("cat-file", "-t", ancestor) == "commit"
    missing = subprocess.run(
        ["git", "cat-file", "-e", f"{future}^{{commit}}"],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert git("branch", "--format=%(refname:short)") in {"main", "master"}
    assert git("tag", "-l") == "old-tag"
    assert git("remote") == ""
