from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from scripts.tools import login_sif_preheat_watchdog


def _args(tmp_path: Path, **overrides):
    data = {
        "config": "config.yaml",
        "ulhpc_config": str(tmp_path / "ulhpc.yaml"),
        "sif_cache_dir": "/scratch/test/sif-cache",
        "apptainer_bin": "/opt/apptainer/bin/apptainer",
        "apptainer_cache_dir": "/scratch/test/apptainer-cache-login",
        "apptainer_tmp_dir": "/scratch/test/apptainer-tmp-login",
        "state_file": str(tmp_path / "state.json"),
        "log_dir": str(tmp_path / "logs"),
        "batch_size": 2,
        "timeout": 123,
        "max_attempts": 1,
        "retry_backoff": 0,
        "check_interval": 0,
        "max_runs": 1,
        "max_no_progress_runs": 2,
        "dry_run": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_build_preheat_command_uses_batch_and_scratch_dirs(tmp_path: Path) -> None:
    args = _args(tmp_path)

    command = login_sif_preheat_watchdog.build_preheat_command(
        args,
        Path("/repo/config.yaml"),
        batch_size=3,
    )

    assert "scripts/tools/login_apptainer_sif_preheat.py" in command[1]
    assert "--missing-only" in command
    assert command[command.index("--limit") + 1] == "3"
    assert command[command.index("--timeout") + 1] == "123"
    assert command[command.index("--apptainer-cache-dir") + 1] == (
        "/scratch/test/apptainer-cache-login"
    )
    assert command[command.index("--apptainer-tmp-dir") + 1] == (
        "/scratch/test/apptainer-tmp-login"
    )


def test_watchdog_marks_completed_when_cache_is_complete(
    tmp_path: Path, monkeypatch
) -> None:
    args = _args(tmp_path, max_runs=0)
    monkeypatch.setattr(login_sif_preheat_watchdog, "parse_args", lambda: args)
    monkeypatch.setattr(
        login_sif_preheat_watchdog,
        "_sif_cache_dir",
        lambda config_path, override: ("/scratch/test/sif-cache", 2),
    )
    monkeypatch.setattr(login_sif_preheat_watchdog, "remote_sif_count", lambda *args: 2)

    assert login_sif_preheat_watchdog.main() == 0

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "completed"
    assert state["last_sif_count"] == 2


def test_watchdog_records_progress_after_batch(tmp_path: Path, monkeypatch) -> None:
    args = _args(tmp_path, max_runs=1, max_no_progress_runs=2)
    counts = iter([1, 2])
    monkeypatch.setattr(login_sif_preheat_watchdog, "parse_args", lambda: args)
    monkeypatch.setattr(
        login_sif_preheat_watchdog,
        "_sif_cache_dir",
        lambda config_path, override: ("/scratch/test/sif-cache", 3),
    )
    monkeypatch.setattr(
        login_sif_preheat_watchdog,
        "remote_sif_count",
        lambda *args: next(counts),
    )
    monkeypatch.setattr(login_sif_preheat_watchdog, "run_batch", lambda *args: 0)

    assert login_sif_preheat_watchdog.main() == 2

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_sif_count"] == 2
    assert state["no_progress_runs"] == 0
    assert state["last_returncode"] == 0
    assert state["last_error"] == "max runs reached before cache completion"


def test_watchdog_blocks_after_repeated_no_progress(tmp_path: Path, monkeypatch) -> None:
    args = _args(tmp_path, max_runs=0, max_no_progress_runs=1)
    monkeypatch.setattr(login_sif_preheat_watchdog, "parse_args", lambda: args)
    monkeypatch.setattr(
        login_sif_preheat_watchdog,
        "_sif_cache_dir",
        lambda config_path, override: ("/scratch/test/sif-cache", 3),
    )
    monkeypatch.setattr(login_sif_preheat_watchdog, "remote_sif_count", lambda *args: 1)
    monkeypatch.setattr(login_sif_preheat_watchdog, "run_batch", lambda *args: 1)

    assert login_sif_preheat_watchdog.main() == 2

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "blocked"
    assert state["no_progress_runs"] == 1
    assert state["last_error"] == "batch rc=1; no SIF cache progress"
