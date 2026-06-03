"""Tests for OpenCode analysis client helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.analysis.opencode_client import is_rate_limit_error, run_opencode
from src.config import AnalysisConfig
from src.exceptions import TaskError


def test_is_rate_limit_error_detects_common_provider_messages():
    assert is_rate_limit_error("HTTP 429 Too Many Requests")
    assert is_rate_limit_error("quota exceeded, retry later")
    assert not is_rate_limit_error("File not found")


def test_run_opencode_constructs_command_with_prompt_before_files(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="When A, do B because C.", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = AnalysisConfig(
        backend="opencode",
        model="kimi-for-coding/k2p6",
        opencode_bin="opencode",
        opencode_xdg_data_home=str(tmp_path / "data"),
    )

    result = run_opencode(
        config=cfg,
        prompt="PROMPT",
        cwd=tmp_path,
        files=[tmp_path / "a.txt", tmp_path / "b.txt"],
    )

    assert result.stdout == "When A, do B because C."
    assert captured["cmd"][:7] == [
        "opencode",
        "run",
        "--pure",
        "--model",
        "kimi-for-coding/k2p6",
        "--dir",
        str(tmp_path),
    ]
    assert captured["cmd"][7] == "PROMPT"
    assert captured["cmd"][8:] == [f"--file={tmp_path / 'a.txt'}", f"--file={tmp_path / 'b.txt'}"]
    assert captured["env"]["XDG_DATA_HOME"] == str(tmp_path / "data")


def test_run_opencode_retries_rate_limit(monkeypatch, tmp_path: Path):
    calls = []
    sleeps = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="429 rate limit")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = AnalysisConfig(
        backend="opencode",
        model="kimi-for-coding/k2p6",
        opencode_xdg_data_home=str(tmp_path / "data"),
        rate_limit_sleep_seconds=5,
        max_retries=1,
    )

    result = run_opencode(
        config=cfg,
        prompt="PROMPT",
        cwd=tmp_path,
        sleep_func=sleeps.append,
    )

    assert result.stdout == "ok"
    assert len(calls) == 2
    assert sleeps == [5]


def test_run_opencode_non_rate_limit_failure_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        subprocess,
        "run",
        MagicMock(return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="bad file")),
    )
    cfg = AnalysisConfig(
        backend="opencode",
        model="kimi-for-coding/k2p6",
        opencode_xdg_data_home=str(tmp_path / "data"),
    )

    with pytest.raises(TaskError, match="opencode failed"):
        run_opencode(config=cfg, prompt="PROMPT", cwd=tmp_path)
