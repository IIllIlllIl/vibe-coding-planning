"""Compute diagnostics reuse transport and retain failed checks without labels."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.tools import prepare_swe_rpg_runtime as prep
from src.exceptions import ControllerYield
from src.swe_rpg_pce.config import load_swe_rpg_pce_config


CONFIG = Path("configs/swe_rpg_runtime_gepa62_v1_20261025.yaml")


@pytest.mark.parametrize(("path", "task_count"), [
    (CONFIG, 62),
    (Path("configs/swe_rpg_runtime_reaccept11_v2_20261026.yaml"), 11),
    (Path("configs/swe_rpg_runtime_reaccept8_v3_20261026.yaml"), 8),
])
def test_no_model_config_and_whole_wave_use_existing_transport(tmp_path, monkeypatch, path, task_count):
    config = load_swe_rpg_pce_config(path, require_api_keys=False)
    config = replace(config, run_dir=tmp_path)
    monkeypatch.setattr(prep, "load_swe_rpg_pce_config", lambda *a, **k: config)
    monkeypatch.setattr(prep, "identity", lambda *a: "frozen")
    result = prep.coordinate(path)
    script = Path(result["script"]).read_text()
    assert script.startswith("#!/bin/bash -l\n")
    assert result["tasks"] == task_count and result["model_calls"] == 0
    assert "#SBATCH --array=" + ",".join(map(str, range(task_count))) + "%3\n" in script
    assert "#SBATCH --mem=4G" in script and "#SBATCH --cpus-per-task=1" in script
    assert f"#SBATCH --time={config.hpc.time}" in script
    assert "source \"${ENV_FILE}\"" not in script
    assert "scripts.tools.prepare_swe_rpg_runtime" in script
    def run(self, **kwargs):
        assert len(kwargs["tasks"]) == task_count
        raise ControllerYield(batch_dir=str(tmp_path), job_id="123", reason="waiting")
    monkeypatch.setattr(prep.SlurmTaskBatch, "run", run)
    assert prep.coordinate(path, submit=True)["job_id"] == "123"


@pytest.mark.parametrize("failed", [None, "history", "calibration"])
def test_worker_uses_existing_entries_and_keeps_independent_checks(tmp_path, monkeypatch, failed):
    monkeypatch.setattr(prep, "load_swe_rpg_pce_config", lambda *a, **k: SimpleNamespace(instance_ids=["one"]))
    monkeypatch.setattr(prep, "identity", lambda *a: "frozen")
    task = tmp_path / "task.json"
    task.write_text(json.dumps(dict(fingerprint="frozen", instance_id="one", role="rpg_runtime_preparation")))
    seen = []
    def run(command, **kwargs):
        module = command[2]
        kind = "history" if "preheat" in module else "calibration"
        seen.append(kind)
        output = Path(command[command.index("--output-dir") + 1])
        output.mkdir(parents=True)
        name = "history_preheat_summary.json" if kind == "history" else "report.json"
        payload = {"records": [{"instance_id": "one", "status": "cached"}]} if kind == "history" else {"passed": kind != failed}
        (output / name).write_text(json.dumps(payload))
        return SimpleNamespace(returncode=2 if kind == failed else 0)
    monkeypatch.setattr(prep.subprocess, "run", run)
    result = prep.worker(CONFIG, task, tmp_path / "output.json", tmp_path / "attempt")
    assert seen == ["history", "calibration"]
    assert result["status"] == "completed" and result["model_calls"] == 0
    for kind, check in result["checks"].items():
        assert check["passed"] == (kind != failed)
        assert len(check["report_sha256"]) == 64
    assert "score" not in result


def test_fingerprint_binds_inputs_and_diagnostic_sources():
    config = load_swe_rpg_pce_config(CONFIG, require_api_keys=False)
    assert len(prep.identity(config)) == 64
