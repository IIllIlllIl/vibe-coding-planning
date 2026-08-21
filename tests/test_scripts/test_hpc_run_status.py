from __future__ import annotations

import json
from pathlib import Path
import subprocess

from scripts import hpc_run_status


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


def test_query_uses_read_only_ssh_and_parses_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(hpc_run_status, "REPO_ROOT", tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "mode: polybench_pcce\npaths:\n  run_dir: output/run\n",
        encoding="utf-8",
    )
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "host: example.invalid\nport: 8022\nuser: tester\nssh_key: ''\n",
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
    assert calls[0][:4] == ["ssh", "-p", "8022", "tester@example.invalid"]
    assert "output/run/evaluator_repairs/repair-one" in calls[0][-1]
