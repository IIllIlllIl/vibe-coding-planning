"""Synthetic auth only: no real credentials, CLI, models or containers."""
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.exceptions import PermanentProviderError
from src.optimization import codex_cli_runtime as runtime
from src.optimization.hpc.worker_contract import worker_failure
from src.swe_verified_pce.worker import _retry_disposition


def _seed(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text('{"synthetic_generation":0}\n')
    return source


def _generation(path):
    return json.loads(path.read_bytes())["synthetic_generation"]


def _state(source):
    return source.with_name(f".{source.name}.vibe-state")


def test_missing_auth_blocks_before_creating_a_session(tmp_path):
    with pytest.raises(runtime.CodexConfigurationError) as caught:
        with runtime._isolated_codex_environment({"codex_auth_file": str(tmp_path / "missing.json")}):
            pytest.fail("missing auth must fail before starting an Agent")
    assert _retry_disposition(caught.value, stage="pce_execution") == "block_run"


@pytest.mark.parametrize("fail", [False, True])
def test_cli_refresh_is_preserved_on_success_and_exception(tmp_path, fail):
    source = _seed(tmp_path)
    original_error = ValueError("original Agent validation failure")
    try:
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            home = Path(env["CODEX_HOME"])
            refreshed = b'{"synthetic_generation": 1}\n\n'
            (home / "auth.json").write_bytes(refreshed)
            (home / "history.jsonl").write_text("previous case must remain private")
            if fail:
                raise original_error
    except ValueError as error:
        assert fail and error is original_error
    assert source.read_bytes() == refreshed
    assert source.stat().st_mode & 0o777 == 0o600
    assert not home.exists() and not (_state(source) / "lease.json").exists()
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        next_home = Path(env["CODEX_HOME"])
        assert next_home != home and not (next_home / "history.jsonl").exists()
        assert (next_home / "auth.json").read_bytes() == refreshed


def test_external_login_wins_over_stale_inflight_auth(tmp_path):
    source = _seed(tmp_path)
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        (Path(env["CODEX_HOME"]) / "auth.json").write_text('{"synthetic_generation":1}')
        source.write_text('{"synthetic_generation":99}')
    assert _generation(source) == 99


def test_external_logout_is_not_undone(tmp_path):
    source = _seed(tmp_path)
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}):
        source.unlink()
    assert not source.exists()


def test_setup_failure_removes_unused_auth_copy(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    atomic = runtime._atomic_private_bytes
    def fail(path, raw):
        if path.name == "lease.json":
            raise OSError("synthetic setup failure")
        atomic(path, raw)
    monkeypatch.setattr(runtime, "_atomic_private_bytes", fail)
    with pytest.raises(OSError, match="setup failure"):
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}):
            pytest.fail("Agent must not start after setup failure")
    assert not list(_state(source).glob("session-*"))
    assert not (_state(source) / "lease.json").exists() and _generation(source) == 0


def test_failed_writeback_retains_private_recovery_lease(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    atomic = runtime._atomic_private_bytes
    def fail(path, raw):
        if path == source:
            raise OSError("synthetic private-store outage")
        atomic(path, raw)
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_atomic_private_bytes", fail)
        with pytest.raises(runtime.CodexConfigurationError, match="recovery lease retained"):
            with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
                home = Path(env["CODEX_HOME"])
                (home / "auth.json").write_text('{"synthetic_generation":1}')
    assert home.exists() and (_state(source) / "lease.json").is_file()
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        assert _generation(Path(env["CODEX_HOME"]) / "auth.json") == 1
    assert not home.exists() and _generation(source) == 1


def _increment_stream(source, acquired, release, result):
    with runtime._isolated_codex_environment({"codex_auth_file": source}) as env:
        path = Path(env["CODEX_HOME"]) / "auth.json"
        generation = _generation(path)
        acquired.set()
        if release is not None and not release.wait(10):
            raise RuntimeError("test synchronization timed out")
        path.write_text(json.dumps({"synthetic_generation": generation + 1}))
        result.put(generation)


def _crash_after_refresh(source):
    with runtime._isolated_codex_environment({"codex_auth_file": source}) as env:
        path = Path(env["CODEX_HOME"])
        (path / "auth.json").write_text('{"synthetic_generation":1}')
        (path / "history.jsonl").write_text("old case")
        os._exit(0)


def test_same_stream_is_exclusive_across_processes_and_hands_off_new_auth(tmp_path):
    source = _seed(tmp_path)
    context = multiprocessing.get_context("spawn")
    first, second, release = (context.Event() for _ in range(3))
    result = context.Queue()
    processes = [context.Process(target=_increment_stream, args=(str(source), first, release, result)),
                 context.Process(target=_increment_stream, args=(str(source), second, None, result))]
    try:
        processes[0].start()
        assert first.wait(10)
        processes[1].start()
        assert not second.wait(0.3)
        release.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert sorted([result.get(timeout=2), result.get(timeout=2)]) == [0, 1]
        assert _generation(source) == 2
    finally:
        release.set()
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.kill()
                process.join(5)


def test_process_exit_recovers_auth_without_exposing_old_case_state(tmp_path):
    source = _seed(tmp_path)
    process = multiprocessing.get_context("spawn").Process(target=_crash_after_refresh, args=(str(source),))
    process.start()
    process.join(10)
    assert process.exitcode == 0
    assert _generation(source) == 0
    lease = json.loads((_state(source) / "lease.json").read_bytes())
    old_home = _state(source) / lease["home"]
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        home = Path(env["CODEX_HOME"])
        assert _generation(home / "auth.json") == 1
        assert home != old_home and not old_home.exists()
        assert not (home / "history.jsonl").exists()
    assert _generation(source) == 1


@pytest.mark.parametrize("corruption", [b"not-json", b"[]"])
def test_corrupt_private_auth_never_replaces_source(tmp_path, corruption):
    source = _seed(tmp_path)
    with pytest.raises(runtime.CodexConfigurationError):
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            (Path(env["CODEX_HOME"]) / "auth.json").write_bytes(corruption)
    assert _generation(source) == 0
    # A fresh external login supersedes the rejected recovery generation.
    source.write_text('{"synthetic_generation":99}')
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        assert _generation(Path(env["CODEX_HOME"]) / "auth.json") == 99


@pytest.mark.parametrize("channel", ["stderr", "error_event"])
def test_native_auth_failure_blocks_workers_and_retains_full_trace(tmp_path, monkeypatch, channel):
    source = _seed(tmp_path)
    code = "refresh_token_reused"
    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1", stderr="")
        if "sandbox" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1,
            stdout=json.dumps({"type": "turn.failed", "error": {"code": code}}) if channel == "error_event" else "",
            stderr=code if channel == "stderr" else "")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(PermanentProviderError) as caught:
        runtime._run_codex_text_agent(model_config={"executor": "codex_cli", "model": "fake",
            "codex_auth_file": str(source)}, working_directory=tmp_path, attempt_dir=tmp_path / "attempt",
            system="Task", user="Input", task="Do task")
    assert _retry_disposition(caught.value, stage="pce_execution") == "block_run"
    failure = worker_failure({}, "started", "agent_execution", caught.value)
    assert failure["status"] == "blocking_failed" and failure["retry_disposition"] == "block_run"
    assert any(item["role"] == "codex_cli" for item in caught.value.trajectory)


def test_successful_agent_and_tool_text_about_auth_does_not_block(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1", stderr="")
        if "sandbox" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        Path(command[command.index("--output-last-message") + 1]).write_text("Handle token_expired in the application.")
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"type": "item.completed",
            "item": {"type": "command_execution", "aggregated_output": "401 Unauthorized refresh_token_reused"}}))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    raw, _ = runtime._run_codex_text_agent(model_config={"executor": "codex_cli", "model": "fake",
        "codex_auth_file": str(source)}, working_directory=tmp_path, attempt_dir=tmp_path / "attempt",
        system="Task", user="Input", task="Do task")
    assert raw == "Handle token_expired in the application."


def test_auth_writeback_failure_retains_completed_agent_artifact_and_trace(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    atomic = runtime._atomic_private_bytes
    def persist(path, raw):
        if path == source:
            raise OSError("synthetic store outage")
        atomic(path, raw)
    monkeypatch.setattr(runtime, "_atomic_private_bytes", persist)
    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1", stderr="")
        if "sandbox" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        (Path(kwargs["env"]["CODEX_HOME"]) / "auth.json").write_text('{"synthetic_generation":1}')
        Path(command[command.index("--output-last-message") + 1]).write_bytes(b"Original final output.\n\n")
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}\n', stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    with pytest.raises(runtime.CodexConfigurationError) as caught:
        runtime._run_codex_text_agent(model_config={"executor": "codex_cli", "model": "fake",
            "codex_auth_file": str(source)}, working_directory=tmp_path, attempt_dir=tmp_path / "attempt",
            system="Task", user="Input", task="Do task")
    assert caught.value.retry_disposition == "block_run"
    assert (tmp_path / "attempt/codex_final_response.json").read_bytes() == b"Original final output.\n\n"
    assert caught.value.trajectory[-1] == {"role": "assistant", "content": "Original final output.\n\n"}
    assert caught.value.trajectory[3]["content"]["events"] == [{"type": "turn.completed"}]
