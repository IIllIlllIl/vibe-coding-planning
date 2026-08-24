from __future__ import annotations

import json

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
