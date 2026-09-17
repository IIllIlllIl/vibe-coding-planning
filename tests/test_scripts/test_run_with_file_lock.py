from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.tools import run_with_file_lock


def test_locked_command_forwards_arguments_and_returncode(
    tmp_path: Path, monkeypatch
) -> None:
    observed: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess:
        observed.append(command)
        assert check is False
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_with_file_lock.py",
            "--lock",
            str(tmp_path / "locks" / "submit.lock"),
            "--",
            "ulhpc-submit",
            "--submit-only",
        ],
    )

    assert run_with_file_lock.main() == 7
    assert observed == [["ulhpc-submit", "--submit-only"]]
    assert (tmp_path / "locks" / "submit.lock").is_file()
