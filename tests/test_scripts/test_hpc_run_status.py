from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

from scripts import hpc_run_status
from scripts.hpc_runtime import AION_REMOTE_EMBEDDED_PYTHON


def test_remote_run_path_targets_optional_repair(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hpc_run_status, "REPO_ROOT", tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "mode: polybench_pcce\npaths:\n  run_dir: output/run\n",
        encoding="utf-8",
    )
    assert hpc_run_status._remote_run_path(config, "~/state", "repair-one").endswith(
        "/output/run/evaluator_repairs/repair-one"
    )


@pytest.mark.parametrize("host,interpreter", [("access-iris.uni.lu", "python3.11"),
                                           ("access-aion.uni.lu", AION_REMOTE_EMBEDDED_PYTHON)])
def test_query_uses_read_only_ssh_and_parses_json(tmp_path: Path, monkeypatch, host, interpreter) -> None:
    monkeypatch.setattr(hpc_run_status, "REPO_ROOT", tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "mode: polybench_pcce\npaths:\n  run_dir: output/run\n",
        encoding="utf-8",
    )
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        f"host: {host}\nport: 8022\nuser: tester\nssh_key: ''\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"exists": True, "totals": {"outputs": 3}}),
            stderr="",
        )

    monkeypatch.setattr(hpc_run_status.subprocess, "run", fake_run)
    payload = hpc_run_status.query(
        config_path=config,
        ulhpc_config=ulhpc,
        remote_root="~/state",
        repair_id="repair-one",
    )

    assert payload["totals"]["outputs"] == 3
    assert calls[0][:4] == ["ssh", "-p", "8022", f"tester@{host}"]
    assert calls[0][-1].startswith(interpreter + " -c ")
    assert "output/run/evaluator_repairs/repair-one" in calls[0][-1]


def test_remote_summary_counts_window_tasks_at_original_authority(tmp_path, monkeypatch, capsys):
    batch = tmp_path / "hpc_tasks/pce/fingerprint"
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    for index in range(15):
        write(batch / "tasks" / f"task_{index:04d}.json", {"task_index": index})
        if index < 9:
            write(batch / "outputs" / f"task_{index:04d}.json", {"status": "completed"})
    write(batch / "execution_windows/window_0000_0008/task_state.json", {"phase": "COMPLETE"})
    write(batch / "execution_windows/window_0008_0015/task_state.json", {"phase": "SUBMITTED"})
    write(batch / "attempts/task_0009/attempt_01/failure.json", {"status": "failed"})
    # The duplicate failure.json is no longer produced; primary envelopes
    # must still be counted without double-counting legacy copies.
    write(batch / "failed_outputs/attempt_01/task_0009.json", {"status": "failed"})
    write(batch / "failed_outputs/attempt_01/task_0010.json", {"status": "failed"})
    ordinary = tmp_path / "hpc_tasks/reflector/other"
    write(ordinary / "task_state.json", {"phase": "COMPLETE"})
    for index in range(2):
        write(ordinary / "tasks" / f"task_{index:04d}.json", {})
    write(ordinary / "outputs/task_0000.json", {})
    write(batch / "attempts/task_0000/attempt_01/workspaces/repo/.venv/task_state.json", {})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
    original_scandir = os.scandir

    def bounded_scandir(path):
        if isinstance(path, (str, Path)) and Path(path).is_relative_to(tmp_path):
            assert "workspaces" not in Path(path).relative_to(tmp_path).parts
        return original_scandir(path)

    monkeypatch.setattr(sys, "argv", ["status", str(tmp_path), "tester"])
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, stdout="", stderr=""))
    with monkeypatch.context() as guard:
        guard.setattr(os, "scandir", bounded_scandir)
        exec(compile(hpc_run_status.REMOTE_SCRIPT, "<remote-status>", "exec"), {})
    payload = json.loads(capsys.readouterr().out)
    windows = [row for row in payload["batches"] if "execution_windows" in row["path"]]
    assert [(row["tasks"], row["outputs"], row["failure_records"]) for row in windows] == [(8, 8, 0), (7, 1, 2)]
    assert all(row["logical_batch_tasks"] == 15 for row in windows)
    assert payload["totals"]["tasks"] == 17 and payload["totals"]["outputs"] == 10
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*.json")}
