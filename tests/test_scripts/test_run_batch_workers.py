"""Tests for the bounded PCT/PCC batch scheduler."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "run_batch_workers",
    REPO_ROOT / "scripts" / "internal" / "run_batch_workers.py",
)
batch = importlib.util.module_from_spec(spec)
sys.modules["run_batch_workers"] = batch
spec.loader.exec_module(batch)


def _result(
    *,
    index: int,
    instance_id: str,
    success: bool = True,
    storage_error: bool = False,
) -> batch.InstanceResult:
    return batch.InstanceResult(
        index=index,
        instance_id=instance_id,
        returncode=0 if success else 1,
        elapsed_seconds=1,
        result_exists=success,
        resolved=False if success else None,
        docker_storage_error=storage_error,
        log_path=f"logs/{instance_id}.log",
    )


def test_parallelism_is_bounded_and_all_instances_run(tmp_path):
    active = 0
    peak = 0
    lock = threading.Lock()

    def run_one(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return _result(
            index=kwargs["index"],
            instance_id=kwargs["instance_id"],
        )

    summary = batch.run_batch(
        instances=["repo__1", "repo__2", "repo__3", "repo__4"],
        config=tmp_path / "config.yaml",
        batch_id="batch",
        dataset_short="dataset",
        output_root=tmp_path / "output",
        log_dir=tmp_path / "logs",
        master_log=tmp_path / "master.log",
        parallel=2,
        run_one=run_one,
    )

    assert peak == 2
    assert summary["started"] == 4
    assert summary["completed"] == 4
    assert summary["failed"] == 0


def test_existing_result_is_skipped(tmp_path):
    result_path = (
        tmp_path
        / "output"
        / "dataset"
        / "batch"
        / "repo__done"
        / "result.json"
    )
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({"plans": []}), encoding="utf-8")
    called: list[str] = []

    def run_one(**kwargs):
        called.append(kwargs["instance_id"])
        return _result(
            index=kwargs["index"],
            instance_id=kwargs["instance_id"],
        )

    summary = batch.run_batch(
        instances=["repo__done", "repo__pending"],
        config=tmp_path / "config.yaml",
        batch_id="batch",
        dataset_short="dataset",
        output_root=tmp_path / "output",
        log_dir=tmp_path / "logs",
        master_log=tmp_path / "master.log",
        parallel=2,
        run_one=run_one,
    )

    assert called == ["repo__pending"]
    assert summary["skipped"] == 1
    assert summary["completed"] == 1


def test_storage_error_stops_new_submissions(tmp_path):
    started: list[str] = []
    release_second = threading.Event()

    def run_one(**kwargs):
        instance_id = kwargs["instance_id"]
        started.append(instance_id)
        if instance_id == "repo__first":
            return _result(
                index=kwargs["index"],
                instance_id=instance_id,
                success=False,
                storage_error=True,
            )
        release_second.wait(timeout=0.05)
        return _result(
            index=kwargs["index"],
            instance_id=instance_id,
        )

    summary = batch.run_batch(
        instances=[
            "repo__first",
            "repo__second",
            "repo__third",
            "repo__fourth",
        ],
        config=tmp_path / "config.yaml",
        batch_id="batch",
        dataset_short="dataset",
        output_root=tmp_path / "output",
        log_dir=tmp_path / "logs",
        master_log=tmp_path / "master.log",
        parallel=2,
        run_one=run_one,
    )
    release_second.set()

    assert set(started) == {"repo__first", "repo__second"}
    assert summary["docker_storage_fatal"] is True
    assert summary["unscheduled"] == 2
    assert summary["failed"] == 1


def test_worker_exception_is_counted_as_failure(tmp_path):
    def run_one(**kwargs):
        raise RuntimeError("worker crashed")

    summary = batch.run_batch(
        instances=["repo__broken"],
        config=tmp_path / "config.yaml",
        batch_id="batch",
        dataset_short="dataset",
        output_root=tmp_path / "output",
        log_dir=tmp_path / "logs",
        master_log=tmp_path / "master.log",
        parallel=1,
        run_one=run_one,
    )

    assert summary["started"] == 1
    assert summary["failed"] == 1


def test_worker_command_propagates_docker_parallel_limit(tmp_path):
    log_dir = tmp_path / "logs"

    with (
        patch.object(batch.shutil, "which", return_value=None),
        patch.object(
            batch.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=1),
        ) as mock_run,
    ):
        result = batch._run_one(
            index=1,
            total=1,
            instance_id="repo__task",
            config=tmp_path / "config.yaml",
            batch_id="batch",
            dataset_short="dataset",
            output_root=tmp_path / "output",
            log_dir=log_dir,
            parallel=3,
        )

    command = mock_run.call_args.args[0]
    flag_index = command.index("--docker-max-concurrent")
    assert command[flag_index + 1] == "3"
    assert result.returncode == 1


def test_parallel_one_preserves_serial_execution(tmp_path):
    active = 0
    peak = 0

    def run_one(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.01)
        active -= 1
        return _result(
            index=kwargs["index"],
            instance_id=kwargs["instance_id"],
        )

    summary = batch.run_batch(
        instances=["repo__1", "repo__2"],
        config=tmp_path / "config.yaml",
        batch_id="batch",
        dataset_short="dataset",
        output_root=tmp_path / "output",
        log_dir=tmp_path / "logs",
        master_log=tmp_path / "master.log",
        parallel=1,
        run_one=run_one,
    )

    assert peak == 1
    assert summary["completed"] == 2
