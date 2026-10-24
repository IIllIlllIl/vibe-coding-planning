"""Accepted parallel auth policy; synthetic credentials and no model calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace

import pytest

from src.exceptions import PermanentProviderError
from src.optimization import codex_cli_runtime as runtime
from src.optimization.hpc.worker_contract import worker_failure
from src.swe_verified_pce.worker import _retry_disposition


def _seed(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text('{"synthetic_generation":0}\n')
    source.chmod(0o600)
    return source


def _state(source):
    return source.with_name(f".{source.name}.vibe-state")


def test_missing_auth_blocks_before_creating_a_session(tmp_path):
    with pytest.raises(runtime.CodexConfigurationError) as caught:
        with runtime._isolated_codex_environment({"codex_auth_file": str(tmp_path / "missing.json")}):
            pytest.fail("missing auth must fail before starting an Agent")
    assert _retry_disposition(caught.value, stage="pce_execution") == "block_run"


@pytest.mark.parametrize("raw", [b"not-json", b"[]"])
def test_invalid_source_cache_blocks_before_creating_a_session(tmp_path, monkeypatch, raw):
    source = _seed(tmp_path)
    source.write_bytes(raw)
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid auth must fail before creating private state")
    monkeypatch.setattr(runtime.tempfile, "TemporaryDirectory", unexpected)
    with pytest.raises(runtime.CodexConfigurationError):
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}):
            pytest.fail("invalid auth must not start an Agent")


def test_symlink_source_cache_is_rejected(tmp_path):
    source = _seed(tmp_path)
    linked = tmp_path / "linked.json"
    linked.symlink_to(source)
    with pytest.raises(runtime.CodexConfigurationError, match="regular file"):
        with runtime._isolated_codex_environment({"codex_auth_file": str(linked)}):
            pytest.fail("source validation must remain enabled")


@pytest.mark.parametrize("fail", [False, True])
def test_private_refresh_is_discarded_without_source_writeback_on_success_or_failure(tmp_path, fail):
    source = _seed(tmp_path)
    original = source.read_bytes()
    original_error = ValueError("original Agent validation failure")
    try:
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            home = Path(env["CODEX_HOME"])
            (home / "auth.json").write_text('{"synthetic_generation":1}')
            (home / "history.jsonl").write_text("previous case must remain private")
            if fail:
                raise original_error
    except ValueError as error:
        assert fail and error is original_error
    assert source.read_bytes() == original and not home.exists()
    assert not _state(source).exists()
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        next_home = Path(env["CODEX_HOME"])
        assert next_home != home and not (next_home / "history.jsonl").exists()
        assert (next_home / "auth.json").read_bytes() == original


def test_external_login_is_not_overwritten(tmp_path):
    source = _seed(tmp_path)
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        (Path(env["CODEX_HOME"]) / "auth.json").write_text('{"synthetic_generation":1}')
        source.write_text('{"synthetic_generation":99}')
    assert json.loads(source.read_bytes())["synthetic_generation"] == 99


def test_external_logout_is_not_undone(tmp_path):
    source = _seed(tmp_path)
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}):
        source.unlink()
    assert not source.exists()


def test_setup_failure_cleans_only_its_private_directory(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    opened = []
    original_open = runtime.os.open
    def fail(path, *args, **kwargs):
        if Path(path).name == "auth.json":
            opened.append(Path(path))
            raise OSError("synthetic copy failure")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(runtime.os, "open", fail)
    with pytest.raises(OSError, match="copy failure"):
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}):
            pytest.fail("Agent must not start after copy failure")
    assert len(opened) == 1 and not opened[0].parent.exists()
    assert source.is_file() and not _state(source).exists()


def test_missing_private_directory_does_not_mask_original_agent_error(tmp_path):
    source = _seed(tmp_path)
    original_error = ValueError("original Agent failure")
    with pytest.raises(ValueError) as caught:
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            home = Path(env["CODEX_HOME"])
            assert home.name.startswith("vibe-codex-home-") and home != tmp_path
            shutil.rmtree(home)
            raise original_error
    assert caught.value is original_error and source.is_file()


@pytest.mark.parametrize("raw", [b"not-json", b"[]"])
def test_corrupt_private_cache_cannot_change_source_or_break_cleanup(tmp_path, raw):
    source = _seed(tmp_path)
    initial = source.read_bytes()
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        home = Path(env["CODEX_HOME"])
        (home / "auth.json").write_bytes(raw)
    assert source.read_bytes() == initial and not home.exists()


def test_existing_legacy_lease_is_neither_recovered_nor_deleted(tmp_path):
    source = _seed(tmp_path)
    state = _state(source)
    state.mkdir(mode=0o700)
    old_home = state / "session-existing"
    old_home.mkdir(mode=0o700)
    (old_home / "auth.json").write_text('{"synthetic_generation":99}')
    lease = state / "lease.json"
    lease.write_text('{"home":"session-existing","synthetic_only":true}')
    original_lease = lease.read_bytes()
    with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        assert Path(env["CODEX_HOME"]) != old_home
        assert json.loads((Path(env["CODEX_HOME"]) / "auth.json").read_bytes())["synthetic_generation"] == 0
    assert lease.read_bytes() == original_lease and old_home.is_dir()


def test_fifty_same_source_threads_overlap_with_private_permissions_and_independent_cleanup(tmp_path):
    source = _seed(tmp_path)
    initial = source.read_bytes()
    barrier = threading.Barrier(50)
    def call(index):
        with runtime._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            home = Path(env["CODEX_HOME"])
            private = home / "auth.json"
            assert home.stat().st_mode & 0o777 == 0o700
            assert private.stat().st_mode & 0o777 == 0o600
            assert private.read_bytes() == initial
            private.write_text(json.dumps({"synthetic_generation": index + 1}))
            (home / "case.json").write_text(str(index))
            barrier.wait(timeout=10)
            assert (home / "case.json").read_text() == str(index)
            return home
    with ThreadPoolExecutor(max_workers=50) as executor:
        homes = list(executor.map(call, range(50)))
    assert len(set(homes)) == 50 and all(not home.exists() for home in homes)
    assert source.read_bytes() == initial and not _state(source).exists()


def _process(source, directory, channel):
    tempfile.tempdir = directory
    with runtime._isolated_codex_environment({"codex_auth_file": source}) as env:
        home = Path(env["CODEX_HOME"])
        channel.send(str(home))
        if not channel.poll(10):
            raise RuntimeError("synthetic process synchronization timeout")
        channel.recv()
        assert home.is_dir() and (home / "auth.json").is_file()


def test_same_source_processes_overlap_and_termination_cannot_delete_peer_state(tmp_path):
    source = _seed(tmp_path)
    context = multiprocessing.get_context("spawn")
    channels = [context.Pipe() for _ in range(2)]
    processes = [context.Process(target=_process, args=(str(source), str(tmp_path), pair[1])) for pair in channels]
    homes = []
    try:
        for process, pair in zip(processes, channels):
            process.start()
            assert pair[0].poll(8), "same-source process must not wait for another call's auth lock"
            homes.append(Path(pair[0].recv()))
        assert homes[0] != homes[1] and all(home.is_dir() for home in homes)
        processes[0].kill()
        processes[0].join(5)
        assert homes[1].is_dir()
        channels[1][0].send("release")
        processes[1].join(8)
        assert processes[1].exitcode == 0 and not homes[1].exists()
        # SIGKILL bypasses Python cleanup; no other call reclaims this orphan.
        assert homes[0].is_dir() and not _state(source).exists()
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.kill()
                process.join(5)
        for pair in channels:
            for channel in pair:
                channel.close()
        for home in homes:
            if home.exists():
                assert home.parent == tmp_path and home.name.startswith("vibe-codex-home-")
                shutil.rmtree(home)


@pytest.mark.parametrize("channel", ["stderr", "error_event"])
@pytest.mark.parametrize("code", ["refresh_token_reused", "token_expired"])
def test_native_auth_failure_still_blocks_workers_and_retains_full_trace(tmp_path, monkeypatch, channel, code):
    source = _seed(tmp_path)
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


def test_private_auth_change_does_not_alter_completed_agent_output_or_trace(tmp_path, monkeypatch):
    source = _seed(tmp_path)
    initial = source.read_bytes()
    original = "Original final output.\n\n"
    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1", stderr="")
        if "sandbox" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        (Path(kwargs["env"]["CODEX_HOME"]) / "auth.json").write_text('{"synthetic_generation":1}')
        Path(command[command.index("--output-last-message") + 1]).write_bytes(original.encode())
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}\n', stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    raw, trajectory = runtime._run_codex_text_agent(model_config={"executor": "codex_cli", "model": "fake",
        "codex_auth_file": str(source)}, working_directory=tmp_path, attempt_dir=tmp_path / "attempt",
        system="Task", user="Input", task="Do task")
    assert raw == original and source.read_bytes() == initial
    assert (tmp_path / "attempt/codex_final_response.json").read_bytes() == original.encode()
    assert trajectory[-1] == {"role": "assistant", "content": original}
    assert trajectory[3]["content"]["events"] == [{"type": "turn.completed"}]
