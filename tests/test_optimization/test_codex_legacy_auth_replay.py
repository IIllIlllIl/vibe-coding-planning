"""Replay the exact successful-run auth design using synthetic credentials only.

No real OAuth/provider requests. Passing isolation tests does not certify the
copied OAuth session's refresh lifecycle or authorize a production rollback.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import multiprocessing
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.exceptions import PermanentProviderError
from src.optimization import codex_cli_runtime as current
from src.swe_verified_pce.worker import _retry_disposition
from tests.fixtures import codex_auth_63a799f as legacy


ROOT = Path(__file__).resolve().parents[2]
BASELINE = "63a799f6ff6ddc767e4fb80636b9bbd324c1b84c"


def _fake_source(root):
    source = root / "synthetic-auth.json"
    source.write_text('{"synthetic_generation":0}\n')
    source.chmod(0o600)
    return source


def test_frozen_functions_match_successful_formal_run_exactly():
    source = subprocess.check_output(["git", "show",
        BASELINE + ":src/optimization/codex_cli_runtime.py"], cwd=ROOT, text=True)
    def selected(text):
        return {node.name: ast.dump(node, include_attributes=False)
                for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)
                and node.name in {"_codex_auth_file", "_isolated_codex_environment"}}
    assert selected(source) == selected(inspect.getsource(legacy))


def test_fifty_legacy_sessions_overlap_keep_source_unchanged_and_cleanup_independently(tmp_path, monkeypatch):
    source = _fake_source(tmp_path)
    original = source.read_bytes()
    monkeypatch.setattr(legacy.tempfile, "tempdir", str(tmp_path))
    barrier = threading.Barrier(50)
    def call(index):
        with legacy._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            home = Path(env["CODEX_HOME"])
            assert home.stat().st_mode & 0o777 == 0o700
            auth = home / "auth.json"
            assert auth.read_bytes() == original and auth.stat().st_mode & 0o777 == 0o600
            (home / "case.json").write_text(str(index))
            barrier.wait(timeout=10)
            assert (home / "case.json").read_text() == str(index)
            return str(home)
    with ThreadPoolExecutor(max_workers=50) as executor:
        homes = list(executor.map(call, range(50)))
    assert len(set(homes)) == 50 and all(not Path(home).exists() for home in homes)
    assert source.read_bytes() == original
    assert not source.with_name(f".{source.name}.vibe-state").exists()


def _session_process(source, temp_parent, channel):
    tempfile.tempdir = temp_parent
    with legacy._isolated_codex_environment({"codex_auth_file": source}) as env:
        home = Path(env["CODEX_HOME"])
        channel.send(str(home))
        if not channel.poll(10):
            raise RuntimeError("synthetic synchronization timeout")
        channel.recv()
        assert home.is_dir() and (home / "auth.json").is_file()


def test_terminating_one_process_does_not_delete_its_concurrent_peer_home(tmp_path):
    source = _fake_source(tmp_path)
    context = multiprocessing.get_context("spawn")
    # Separate pipes keep forced termination away from shared Event/Queue
    # synchronization locks, which are not safe when an owner is killed.
    channels = [context.Pipe() for _ in range(2)]
    processes = [context.Process(target=_session_process,
        args=(str(source), str(tmp_path), channels[index][1])) for index in range(2)]
    homes = []
    try:
        for index, process in enumerate(processes):
            process.start()
            assert channels[index][0].poll(8)
            homes.append(Path(channels[index][0].recv()))
        assert homes[0] != homes[1] and all(home.is_dir() for home in homes)
        processes[0].kill()
        processes[0].join(5)
        assert homes[1].is_dir()
        channels[1][0].send("release")
        processes[1].join(8)
        assert processes[1].exitcode == 0 and not homes[1].exists()
        # SIGKILL bypasses TemporaryDirectory cleanup: the old design can leave
        # its own orphan, but has no shared lease that deletes another call.
        assert homes[0].is_dir()
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
                for child in home.iterdir():
                    assert child.is_file() and not child.is_symlink()
                    child.unlink()
                home.rmdir()


def test_successful_private_refresh_is_discarded_and_next_session_reuses_seed(tmp_path):
    source = _fake_source(tmp_path)
    with legacy._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        private = Path(env["CODEX_HOME"]) / "auth.json"
        private.write_text('{"synthetic_generation":1}')
    assert json.loads(source.read_text())["synthetic_generation"] == 0
    with legacy._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
        assert json.loads((Path(env["CODEX_HOME"]) / "auth.json").read_text())["synthetic_generation"] == 0


def test_simulated_single_use_refresh_conflict_is_not_prevented(tmp_path):
    source = _fake_source(tmp_path)
    barrier = threading.Barrier(2)
    server_lock = threading.Lock()
    generation = [0]
    def call(_index):
        with legacy._isolated_codex_environment({"codex_auth_file": str(source)}) as env:
            private = Path(env["CODEX_HOME"]) / "auth.json"
            copied = json.loads(private.read_text())["synthetic_generation"]
            barrier.wait(timeout=5)
            with server_lock:
                if copied != generation[0]:
                    return "synthetic_refresh_reused"
                generation[0] += 1
                private.write_text(json.dumps({"synthetic_generation": generation[0]}))
                return "synthetic_refresh_success"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(call, range(2)))
    assert sorted(results) == ["synthetic_refresh_reused", "synthetic_refresh_success"]
    assert json.loads(source.read_text())["synthetic_generation"] == 0


@pytest.mark.parametrize("failure", [None, "refresh_token_reused", "token_expired"])
def test_legacy_context_can_be_injected_without_rolling_back_current_output_and_failure_contracts(tmp_path, monkeypatch, failure):
    source = _fake_source(tmp_path)
    monkeypatch.setattr(current, "_isolated_codex_environment", legacy._isolated_codex_environment)
    raw = "Original Agent final text.\n\n"
    def run(command, **kwargs):
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1", stderr="")
        if "sandbox" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if failure:
            return SimpleNamespace(returncode=1, stderr=failure,
                stdout=json.dumps({"type": "turn.failed", "error": {"code": failure}}))
        Path(command[command.index("--output-last-message") + 1]).write_bytes(raw.encode())
        return SimpleNamespace(returncode=0, stderr="", stdout='{"type":"turn.completed"}\n')
    monkeypatch.setattr(current.subprocess, "run", run)
    arguments = dict(model_config={"executor": "codex_cli", "model": "synthetic",
        "codex_auth_file": str(source), "codex_version": "0.160.1"},
        working_directory=tmp_path, attempt_dir=tmp_path / "attempt",
        system="Synthetic task", user="Synthetic input", task="Synthetic check")
    if failure:
        with pytest.raises(PermanentProviderError) as caught:
            current._run_codex_text_agent(**arguments)
        assert _retry_disposition(caught.value, stage="agent_execution") == "block_run"
        assert any(message["role"] == "codex_cli" for message in caught.value.trajectory)
    else:
        result, trajectory = current._run_codex_text_agent(**arguments)
        assert result == raw and trajectory[-1] == {"role": "assistant", "content": raw}
    assert source.read_text() == '{"synthetic_generation":0}\n'


def replay_real_cli_without_model(binary, root, *, environment_factory=None):
    """Reuse the established loopback/hooks fixture with the production context.

    This never uses real credentials or an external provider. The mock provider
    explicitly disables OpenAI auth, so it cannot validate real OAuth access.
    """
    from scripts.tools.verify_codex_command_policy import verify
    environment_factory = environment_factory or current._isolated_codex_environment
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source = _fake_source(root)
    original = source.read_bytes()
    original_run = subprocess.run
    guard = threading.Lock()
    barrier = threading.Barrier(2)
    intervals = []
    import time
    def run(command, *args, **kwargs):
        if command[0] != binary or len(command) < 2 or command[1] != "exec":
            return original_run(command, *args, **kwargs)
        with environment_factory({"codex_auth_file": str(source)}) as environment:
            home = Path(environment["CODEX_HOME"])
            env = dict(kwargs["env"], CODEX_HOME=str(home))
            barrier.wait(timeout=10)
            started = time.monotonic()
            try:
                return original_run(command, *args, **dict(kwargs, env=env))
            finally:
                with guard:
                    intervals.append((started, time.monotonic(), str(home)))
    with patch.object(subprocess, "run", run), ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(verify, binary, root / str(index), submission=True) for index in range(2)]
        reports = [future.result(timeout=60) for future in futures]
    overlap = min(row[1] for row in intervals) - max(row[0] for row in intervals)
    assert len(intervals) == 2 and overlap > 0
    assert len({row[2] for row in intervals}) == 2
    assert all(not Path(row[2]).exists() for row in intervals)
    assert source.read_bytes() == original
    version = original_run([binary, "--version"], capture_output=True, text=True, check=True)
    return {"baseline_commit": BASELINE, "real_cli_calls": 2,
        "cli_version": version.stdout.strip(),
        "auth_context_module": environment_factory.__module__,
        "overlap_seconds": round(overlap, 3), "private_homes_distinct_and_cleaned": True,
        "source_unchanged": True, "real_oauth_tested": False,
        "external_provider_calls": 0,
        "calls": [{"returncode": report["cli_returncode"], "scripted_requests": report["requests"],
                   "source_deny_feedback": report["denial_returned_to_native_loop"],
                   "submission_feedback": report["submission_feedback_returned"]} for report in reports]}
