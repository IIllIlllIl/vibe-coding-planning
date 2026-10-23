"""Permanent failures cross real Worker envelopes without producing labels."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.exceptions import PermanentProviderError
from src.optimization.config import BehavioralHybridConfig, load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


def _failure():
    error = PermanentProviderError("Synthetic provider authentication failure; login required")
    error.trajectory = [{"role": "codex_cli", "content": {"events": [{"type": "turn.failed"}]}}]
    return error


@pytest.mark.parametrize("role", ["reflector", "curator", "refiner"])
def test_hybrid_worker_blocks_permanent_provider_failure(tmp_path, monkeypatch, role):
    from src.optimization import behavioral_hybrid_worker as worker
    from src.optimization.behavioral_hybrid_runtime import phase_identity
    from tests.test_optimization.test_behavioral_hybrid_runtime import PARENT
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    config = replace(config, hybrid=BehavioralHybridConfig(enabled=True, refiner_enabled=True))
    record = {"instance_id": "case", "split": "train"}
    payload = {"parent": PARENT, "record": record, "records": [record], "split": "train"}
    manifest = {"mode": "behavioral_hybrid_phase", "role": role, "payload": payload,
                "fingerprint": phase_identity(config, role, payload)}
    path, output = tmp_path / "input.json", tmp_path / "result.json"
    atomic_json(path, manifest)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *a: config)
    def fail(*args, **kwargs):
        raise _failure()
    monkeypatch.setattr(worker, "run_local_phase", fail)
    assert worker.run_task(config_path=Path("unused"), manifest_path=path,
        output_path=output, attempt_dir=tmp_path / "attempt") == 1
    result = json.loads(output.read_text())
    assert result["status"] == "blocking_failed"
    assert result["error_type"] == "PermanentProviderError" and result["retry_disposition"] == "block_run"
    assert result["failure_stage"] == "agent_execution" and "agent_output" not in result


def test_verified_worker_does_not_turn_permanent_failure_into_unresolved(tmp_path, monkeypatch):
    from src.swe_verified_pce import worker
    from src.swe_verified_pce.dataset import load_swe_verified_pce_cases
    from tests.test_swe_verified_pce_pcce import _snapshot
    source, images, _ = _snapshot(tmp_path)
    raw = yaml.safe_load(Path("configs/safe_pce_ace_verified_codex_smoke_source_v1_20261023.yaml").read_text())
    raw["paths"].update(dataset_snapshot=str(source), image_manifest=str(images), run_dir=str(tmp_path / "run"))
    raw["paths"].pop("selection_manifest")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(raw))
    case = load_swe_verified_pce_cases(source, images)[0][0]
    manifest, output = tmp_path / "input.json", tmp_path / "result.json"
    atomic_json(manifest, {"case": case.to_dict(), "fingerprint": "same-input", "task_index": 0})
    monkeypatch.setattr(worker, "configure_docker_capacity", lambda *a, **kw: SimpleNamespace())
    def fail(self, case):
        raise _failure()
    monkeypatch.setattr(worker.SWEVerifiedPCERunner, "run", fail)
    assert worker.run_task(config_path=config, task_manifest_path=manifest, output_path=output,
        attempt_dir=tmp_path / "run/attempt_01", checkpoint_dir=tmp_path / "run/checkpoints", attempt=1) == 1
    result = json.loads(output.read_text())
    assert result["error_type"] == "PermanentProviderError"
    assert result["status"] == "blocking_failed" and result["retry_disposition"] == "block_run"
    assert result["task_outcome"] == "unknown" and result["final_validation_label"] is None
    assert result["failure_trajectory"] == _failure().trajectory


def test_behavioral_checker_reuses_common_permanent_failure_contract(tmp_path, monkeypatch):
    from src.optimization import behavioral_checker_worker as worker
    from tests.test_optimization.test_behavioral_offline_foundation import _record
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    record = _record("case", "train", "ACCEPT")
    checker = record["checker_input"]
    repository = checker["repository_proxy"]
    rules = tmp_path / "rules.md"
    rules.write_text("Investigate the proposed approach.")
    manifest = {"mode": "behavioral_checker", "instance_id": "case", "split": "train",
        "fingerprint": "same-input", "rules_path": str(rules), "worker_payload": {
            "task_semantics": record["task_semantics"], "checker_input": checker,
            "repository_materialization": {"mirror_path": str(config.behavioral_repository.repositories_root / "repo.git"),
                "proxy_commit": repository["proxy_commit"]}}}
    path, output = tmp_path / "input.json", tmp_path / "result.json"
    atomic_json(path, manifest)
    monkeypatch.setattr(worker, "load_optimization_config", lambda *a: config)
    class Checker:
        def __init__(self, config):
            pass
        def __call__(self, *args, **kwargs):
            raise _failure()
    monkeypatch.setattr(worker, "BehavioralLocalChecker", Checker)
    assert worker.run_task(config_path=Path("unused"), task_manifest_path=path,
        output_path=output, attempt_dir=tmp_path / "attempt") == 1
    result = json.loads(output.read_text())
    assert result["status"] == "blocking_failed" and result["retry_disposition"] == "block_run"
    assert result["failure_stage"] == "checker_execution" and "checker_output" not in result
    assert result["failure_trajectory"] == _failure().trajectory
