"""Tests for the GEPA fake LLM monitoring server."""

from __future__ import annotations

from pathlib import Path

from src.optimization.config import load_optimization_config
from tests.fakes.fake_litellm_server import (
    FakeLLMServer,
    post_chat_completion,
    read_logged_requests,
)


def test_fake_litellm_server_allows_flash_and_logs_request(tmp_path):
    log_path = tmp_path / "calls.jsonl"
    with FakeLLMServer(log_path=log_path) as server:
        status, payload = post_chat_completion(
            server.url,
            model="deepseek/deepseek-v4-flash",
            messages=[
                {
                    "role": "system",
                    "content": "Write /tmp/gepa_checker_result.json",
                }
            ],
        )

    assert status == 200
    content = payload["choices"][0]["message"]["content"]
    assert "/tmp/gepa_checker_result.json" in content
    calls = read_logged_requests(log_path)
    assert len(calls) == 1
    assert calls[0].model == "deepseek/deepseek-v4-flash"
    assert calls[0].allowed is True


def test_fake_litellm_server_blocks_pro_kimi_and_anthropic(tmp_path):
    log_path = tmp_path / "calls.jsonl"
    with FakeLLMServer(log_path=log_path) as server:
        for model in (
            "deepseek/deepseek-v4-pro",
            "kimi-for-coding/k2p6",
            "anthropic/claude-sonnet-4",
        ):
            status, payload = post_chat_completion(
                server.url,
                model=model,
                messages=[{"role": "user", "content": "hello"}],
            )
            assert status == 400
            assert payload["error"]["type"] == "blocked_model"

    calls = read_logged_requests(log_path)
    assert [call.allowed for call in calls] == [False, False, False]
    assert all(call.blocked_reason for call in calls)


def test_gepa_stub_config_uses_only_fake_flash(monkeypatch):
    monkeypatch.setenv("FAKE_LLM_API_KEY", "dummy")
    config = load_optimization_config(
        Path("tests/fixtures/gepa_verified_rules_stub.yaml")
    )

    assert config.checker.model == "openai/deepseek-v4-flash"
    assert config.reflection.model == "openai/deepseek-v4-flash"
    assert config.checker.api_base == "http://127.0.0.1:18080"
    assert config.reflection.api_base == "http://127.0.0.1:18080"
    assert config.checker.api_key_env == "FAKE_LLM_API_KEY"
    assert config.reflection.api_key_env == "FAKE_LLM_API_KEY"
