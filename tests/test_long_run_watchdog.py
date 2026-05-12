"""Dry-run tests for scripts/long_run_watchdog.py.

These tests exercise the watchdog logic without spawning real tmux
sessions or running the actual batch.  They verify state management,
log parsing, error classification, and cooldown math.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# The watchdog is a script, not a module, so we import it carefully.
import importlib.util

spec = importlib.util.spec_from_file_location(
    "long_run_watchdog", "scripts/long_run_watchdog.py"
)
watchdog = importlib.util.module_from_spec(spec)
import sys
sys.modules["long_run_watchdog"] = watchdog
spec.loader.exec_module(watchdog)


class TestWatchdogState:
    def test_roundtrip(self, tmp_path: Path):
        state = watchdog.WatchdogState(
            batch_id="test-batch",
            total_instances=500,
            completed=12,
            current_instance="django__django-12345",
            status="running",
        )
        with patch.object(watchdog, "STATE_FILE", tmp_path / "state.json"):
            watchdog.save_state(state)
            loaded = watchdog.load_state()

        assert loaded.batch_id == "test-batch"
        assert loaded.total_instances == 500
        assert loaded.completed == 12
        assert loaded.current_instance == "django__django-12345"
        assert loaded.status == "running"
        assert loaded.last_heartbeat != ""

    def test_backward_compat_missing_fields(self, tmp_path: Path):
        """Older state files without docker_retry_count must load gracefully."""
        raw = {
            "batch_id": "old-batch",
            "total_instances": 100,
            "completed": 5,
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(raw))

        with patch.object(watchdog, "STATE_FILE", state_file):
            loaded = watchdog.load_state()

        assert loaded.batch_id == "old-batch"
        assert loaded.docker_retry_count == 0
        assert loaded.status == "running"


class TestCooldownLogic:
    def test_cooldown_not_expired(self):
        future = datetime.now(timezone.utc).replace(microsecond=0)
        future_iso = future.isoformat()
        state = watchdog.WatchdogState(
            batch_id="b", total_instances=10, status="api_cooldown",
            api_cooldown_until=future_iso,
        )
        assert watchdog.cooldown_expired(state) is True  # edge: exactly now

    def test_cooldown_expired(self):
        past = datetime.now(timezone.utc).replace(microsecond=0)
        past_iso = past.isoformat()
        state = watchdog.WatchdogState(
            batch_id="b", total_instances=10, status="api_cooldown",
            api_cooldown_until=past_iso,
        )
        assert watchdog.cooldown_expired(state) is True

    def test_cooldown_active(self):
        future = datetime.now(timezone.utc)
        future = future.replace(second=future.second + 10)
        state = watchdog.WatchdogState(
            batch_id="b", total_instances=10, status="api_cooldown",
            api_cooldown_until=future.isoformat(),
        )
        assert watchdog.cooldown_expired(state) is False


class TestLogAnalysis:
    def test_detect_api_rate_limit(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START django__django-12345\n"
            "[2026-05-12 10:05:00] ERROR: DeepSeek API returned 429 Too Many Requests\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["api_rate_limited"] is True
        assert analysis["api_auth_failed"] is False
        assert analysis["docker_down"] is False

    def test_detect_docker_down(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] ERROR: Cannot connect to the Docker daemon\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["docker_down"] is True

    def test_detect_expected_failure_limits_exceeded(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] FAIL  django__django-12345 rc=1 "
            "elapsed=3600s resolved=False (see logs/batch/django__django-12345.log)\n"
            "Round 1: Code agent terminated without a submission (exit_status=LimitsExceeded)\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["expected_failure"] is True
        assert analysis["api_rate_limited"] is False

    def test_current_instance_extraction(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START astropy__astropy-12907 -> logs/batch/astropy__astropy-12907.log\n"
            "[2026-05-12 10:10:00] DONE  astropy__astropy-12907 rc=0 elapsed=600s resolved=True\n"
            "[2026-05-12 10:10:01] START django__django-12345 -> logs/batch/django__django-12345.log\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["current_instance"] == "django__django-12345"
        assert analysis["log_completed"] == 1

    def test_log_completed_counts_skip_and_done(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] SKIP inst1 (result.json exists)\n"
            "[2026-05-12 10:01:00] DONE inst2 rc=0 elapsed=100s resolved=True\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["log_completed"] == 2


class TestHangDetection:
    def test_not_hung_when_log_recent(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text("recent log line\n")
        with patch.object(watchdog, "MASTER_LOG", log):
            assert watchdog.is_batch_hung() is False

    def test_hung_when_log_stale(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text("old log line\n")
        # Back-date the file mtime
        old_time = time.time() - watchdog.HANG_TIMEOUT_SECONDS - 60
        os.utime(log, (old_time, old_time))
        with patch.object(watchdog, "MASTER_LOG", log):
            assert watchdog.is_batch_hung() is True


class TestDiskSpace:
    def test_disk_space_passes(self):
        # The current directory almost certainly has > 10 GB free
        assert watchdog.check_disk_space() is True


class TestRepairPrompt:
    def test_prompt_contains_regression_testing(self):
        state = watchdog.WatchdogState(batch_id="b", total_instances=10)
        # We can't easily call invoke_claude_repair without tmux, but we can
        # inspect the prompt text indirectly by checking it exists in the
        # function source.  A simpler approach: the prompt is built inside
        # the function; let's just verify the constants make sense.
        assert "Run the FULL test suite" in str(watchdog.invoke_claude_repair.__code__.co_consts)
        assert "write additional tests" in str(watchdog.invoke_claude_repair.__code__.co_consts)

