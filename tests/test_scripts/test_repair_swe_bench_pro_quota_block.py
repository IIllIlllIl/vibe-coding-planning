from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.tools.repair_swe_bench_pro_quota_block import repair_quota_block


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path, *, error: str = "Disk quota exceeded") -> Path:
    batch = tmp_path / "persistent" / "batch"
    _write(
        batch / "task_state.json",
        {
            "schema_version": 1,
            "fingerprint": "abc",
            "phase": "BLOCKED",
            "active_attempt": 1,
            "active_job_id": None,
            "last_job_id": "123",
            "terminal_failure": {
                "failure_kind": "blocking_task_output",
                "details": [
                    {"instance_id": "case", "error_type": "FatalError", "error": error}
                ],
            },
        },
    )
    _write(
        batch / "outputs/task_0000.json",
        {
            "status": "blocking_failed",
            "instance_id": "case",
            "error_type": "FatalError",
            "error": error,
            "retry_disposition": "block_run",
        },
    )
    _write(
        batch / "outputs/task_0001.json",
        {"status": "completed", "instance_id": "done"},
    )
    (batch / "attempts/task_0000/attempt_01/workspaces/plan").mkdir(parents=True)
    (batch / "attempts/task_0001/attempt_01/workspaces/code").mkdir(parents=True)
    return batch


def test_quota_repair_preserves_results_and_redirects_future_workspaces(tmp_path: Path) -> None:
    batch = _fixture(tmp_path)
    scratch = tmp_path / "scratch"

    result = repair_quota_block(
        batch_dir=batch,
        scratch_workspace_root=scratch,
        expected_fingerprint="abc",
    )

    state = json.loads((batch / "task_state.json").read_text())
    assert state["phase"] == "SUBMITTED"
    assert state["active_attempt"] == 1
    assert state["active_job_id"] == "123"
    assert "terminal_failure" not in state
    repaired = json.loads((batch / "outputs/task_0000.json").read_text())
    assert repaired["status"] == "retryable_failed"
    assert repaired["retry_disposition"] == "retry_same_phase"
    assert json.loads((batch / "outputs/task_0001.json").read_text())["status"] == "completed"
    assert not (batch / "attempts/task_0000/attempt_01/workspaces").exists()
    link = batch / "attempts/task_0000/attempt_02/workspaces"
    assert link.is_symlink()
    assert link.resolve() == scratch / "task_0000/attempt_02"
    assert len(result["original_blocking_outputs"]) == 1
    original = json.loads(
        (batch / "operational_repairs/quota-attempt-01/original_outputs/task_0000.json").read_text()
    )
    assert original["status"] == "blocking_failed"


def test_quota_repair_refuses_non_quota_block(tmp_path: Path) -> None:
    batch = _fixture(tmp_path, error="base commit mismatch")
    with pytest.raises(ValueError, match="not exclusively quota-related"):
        repair_quota_block(
            batch_dir=batch,
            scratch_workspace_root=tmp_path / "scratch",
            expected_fingerprint="abc",
        )
    assert json.loads((batch / "task_state.json").read_text())["phase"] == "BLOCKED"
