from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import yaml

import scripts.swe_bench_pro_preheat_service as service


def _config(tmp_path: Path) -> Path:
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps({"images": ["jefzda/sweap-images:one", "jefzda/sweap-images:two"]}),
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "purpose": "swe_bench_pro_sif_preheat",
                "semantic": {
                    "preheat_images": str(images),
                    "preheat_images_sha256": hashlib.sha256(images.read_bytes()).hexdigest(),
                    "expected_images": 2,
                },
                "operational": {
                    "ulhpc_config": str(tmp_path / "ulhpc.yaml"),
                    "sif_cache_dir": "/scratch/test/sifs",
                    "apptainer_bin": "/opt/apptainer",
                    "apptainer_cache_dir": "/scratch/test/cache",
                    "apptainer_tmp_dir": "/scratch/test/tmp",
                    "provenance_output": "/scratch/test/provenance.json",
                    "failed_output": "/scratch/test/failures.tsv",
                    "lock_file": "/scratch/test/preheat.lock",
                    "pull_timeout_seconds": 3600,
                    "max_attempts": 3,
                    "retry_backoff_seconds": 60,
                    "failure_policy": "skip_and_report",
                    "cleanup_apptainer_cache": False,
                },
                "supervisor": {"session": "pro-test", "log": str(tmp_path / "log")},
            }
        ),
        encoding="utf-8",
    )
    return config


def test_loads_frozen_manifest_and_builds_bounded_command(tmp_path: Path) -> None:
    plan = service._load_config(_config(tmp_path))
    command = service._preheat_command(plan)

    assert "--images-json" in command
    assert "--missing-only" in command
    assert command[command.index("--max-attempts") + 1] == "3"
    assert command[command.index("--timeout") + 1] == "3600"
    assert command[command.index("--lock-file") + 1] == "/scratch/test/preheat.lock"
    assert "--cleanup-apptainer-cache" not in command


def test_start_uses_tmux_and_caffeinate(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    calls: list[list[str]] = []

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[:2] == ["tmux", "has-session"]:
            return subprocess.CompletedProcess(arguments, 1, "", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["service", "start", "--config", str(config)])

    assert service.main() == 0
    launch = calls[-1]
    assert launch[:3] == ["tmux", "new-session", "-d"]
    assert "caffeinate -i -s" in launch[-1]
    assert "--images-json" in launch[-1]
