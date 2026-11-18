"""Durable Native rollouts; auth remains volatile. No provider calls."""
from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from src.optimization import codex_session_runtime as runtime


def rollout(identifier="test-session", **metadata):
    return (json.dumps({"type": "session_meta", "payload": {
        "id": identifier, "source": "exec", "thread_source": "user", **metadata}})
        + '\n{"type":"synthetic_event","text":"原始内容"}\r\n').encode()


class Boundary:
    @contextmanager
    def launch(self, model, environment, attempt):
        yield [], environment, model, attempt


def test_new_owner_selects_live_rollout_after_volatile_home_is_gone(tmp_path):
    auth = tmp_path / "private-auth.json"
    auth.write_text('{"synthetic_auth":true}')
    model = {"codex_auth_file": str(auth)}
    state = tmp_path / "native-session"
    with runtime.PersistentCodexConversation(model_config=model, container=Boundary(),
            attempt_dir=tmp_path / "first", session_dir=state) as first:
        home = Path(first.environment["CODEX_HOME"])
        assert first.session_id is None
        # CLI writes directly through the nested mount, before a turn returns.
        raw = rollout()
        (state / "sessions/main.jsonl").write_bytes(raw)
        assert first.session_evidence() == [{"path": "main.jsonl", "text": raw.decode()}]
    assert not home.exists()
    with runtime.PersistentCodexConversation(model_config=model, container=Boundary(),
            attempt_dir=tmp_path / "second", session_dir=state) as second:
        assert second.session_id == "test-session"
        assert Path(second.environment["CODEX_HOME"]) != home
    assert (state / "sessions/main.jsonl").read_bytes() == raw
    assert not any(path.name in {"auth.json", "config.toml"} for path in state.rglob("*"))
    assert auth.read_text() == '{"synthetic_auth":true}'


def test_legacy_archive_restores_original_bytes_and_subthreads(tmp_path):
    root = tmp_path / "sessions"
    rows = [{"path": "2026/11/17/main.jsonl", "text": rollout().decode()},
            {"path": "2026/11/17/child.jsonl", "text": rollout("child", thread_source={"subagent": {}}).decode()}]
    archive = tmp_path / "archive.json"
    archive.write_text(json.dumps(rows))
    runtime.restore_codex_sessions(root, archive)
    assert runtime.codex_session_id(root) == "test-session"
    for row in rows:
        assert (root / row["path"]).read_bytes() == row["text"].encode()


@pytest.mark.parametrize("relative", ["../auth.jsonl", "/absolute.jsonl", "auth.json"])
def test_archive_cannot_restore_non_rollout_paths(tmp_path, relative):
    archive = tmp_path / "archive.json"
    archive.write_text(json.dumps([{"path": relative, "text": rollout().decode()}]))
    with pytest.raises(runtime.CodexConfigurationError, match="invalid rollout path"):
        runtime.restore_codex_sessions(tmp_path / "sessions", archive)


def test_ambiguous_main_sessions_do_not_select_latest(tmp_path):
    (tmp_path / "one.jsonl").write_bytes(rollout("one"))
    (tmp_path / "two.jsonl").write_bytes(rollout("two"))
    with pytest.raises(runtime.CodexConfigurationError, match="Multiple main"):
        runtime.codex_session_id(tmp_path)


def test_archive_cannot_replace_a_more_recent_live_rollout(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    original = rollout() + b'{"type":"later_event"}\n'
    (root / "main.jsonl").write_bytes(original)
    archive = tmp_path / "archive.json"
    archive.write_text(json.dumps([{"path": "main.jsonl", "text": rollout().decode()}]))
    with pytest.raises(runtime.CodexConfigurationError, match="replace an existing"):
        runtime.restore_codex_sessions(root, archive)
    assert (root / "main.jsonl").read_bytes() == original


@pytest.mark.parametrize("provider_failure", [False, True])
def test_resume_identity_check_cannot_accept_another_thread_or_hide_auth_failure(tmp_path, monkeypatch, provider_failure):
    from src.agents.output_contract import AgentOutputContractError
    from src.exceptions import PermanentProviderError
    model = {"executor": "codex_cli", "model": "gpt-6-sol", "reasoning_effort": "high"}
    conversation = runtime.PersistentCodexConversation(model_config=model, container=Boundary(),
        attempt_dir=tmp_path / "attempt", session_dir=tmp_path / "native")
    conversation.session_id = "original-thread"
    def run(self, **kwargs):
        trajectory = [{"role": "codex_cli", "content": {"events": [
            {"type": "thread.started", "thread_id": "wrong-thread"}], "stderr": ""}}]
        if provider_failure:
            error = AgentOutputContractError("missing thread identity")
            trajectory[0]["content"]["stderr"] = "authentication failed"
            error.trajectory = trajectory
            raise error
        return "original response", trajectory
    monkeypatch.setattr(runtime.CodexConversation, "call_text", run)
    with pytest.raises(PermanentProviderError if provider_failure else AgentOutputContractError):
        conversation.call_text(system="probe", user="probe", task="probe", output_filename="answer.txt")
    assert conversation.session_id == "original-thread"
