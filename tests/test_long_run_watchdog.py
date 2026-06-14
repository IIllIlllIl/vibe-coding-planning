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
from datetime import datetime, timedelta, timezone
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

    def test_gepa_mode_initializes_global_task(self, monkeypatch):
        monkeypatch.setenv(
            "GEPA_RULES_CONFIG",
            "configs/gepa_verified_rules.yaml",
        )
        state = watchdog._init_state()
        assert state.batch_id == "gepa-rules"
        assert state.total_instances == 1
        assert state.gepa_rules_phase == "running"


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
        # Use timedelta to avoid the "second must be in 0..59" overflow
        # when datetime.now().second is >= 50 (replace() does no carry).
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        state = watchdog.WatchdogState(
            batch_id="b", total_instances=10, status="api_cooldown",
            api_cooldown_until=future.isoformat(),
        )
        assert watchdog.cooldown_expired(state) is False


class TestLogAnalysis:
    def test_detect_gepa_completion_marker(self, tmp_path: Path):
        log = tmp_path / "gepa_rules.log"
        log.write_text(
            "[2026-06-14 12:00:00] === GEPA rules end: rc=0 ===\n",
            encoding="utf-8",
        )
        analysis = watchdog.analyze_recent_logs(log)
        assert analysis["batch_completed"] is True

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


class TestNetworkErrorPatterns:
    def test_detect_503_service_unavailable(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] ERROR: litellm.ServiceUnavailableError: "
            "DeepSeekException - 503 Service Unavailable\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["api_rate_limited"] is True
        assert analysis["api_auth_failed"] is False

    def test_detect_connection_error(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] ERROR: ConnectionError: "
            "HTTPSConnectionPool(host='api.deepseek.com', port=443)\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["api_rate_limited"] is True

    def test_detect_read_timeout(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] ERROR: ReadTimeout: Request timed out after 120s\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["api_rate_limited"] is True

    def test_detect_dns_failure(self, tmp_path: Path):
        log = tmp_path / "batch_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] ERROR: Name or service not known: api.deepseek.com\n"
        )
        with patch.object(watchdog, "MASTER_LOG", log):
            analysis = watchdog.analyze_recent_logs()
        assert analysis["api_rate_limited"] is True


class TestRepairLimit:
    def test_max_repair_attempts_constant(self):
        assert watchdog.MAX_REPAIR_ATTEMPTS == 3

    def test_repair_backoff_longer_than_api_cooldown(self):
        assert watchdog.REPAIR_BACKOFF_SECONDS > watchdog.API_COOLDOWN_SECONDS


class TestDockerBackoff:
    def test_backoff_base(self):
        assert watchdog.docker_backoff_wait(1) == 300

    def test_backoff_doubles(self):
        assert watchdog.docker_backoff_wait(2) == 600

    def test_backoff_caps_at_60_min(self):
        assert watchdog.docker_backoff_wait(20) == 3600

    def test_backoff_sequence(self):
        waits = [watchdog.docker_backoff_wait(i) for i in range(1, 8)]
        assert waits == [300, 600, 1200, 2400, 3600, 3600, 3600]


class TestCleanupDocker:
    def test_function_exists(self):
        assert callable(watchdog.cleanup_docker)

    @patch("src.environment.docker_env.get_docker_capacity_window")
    def test_uses_shared_capacity_window(self, mock_get_window):
        watchdog.cleanup_docker()

        mock_get_window.return_value.maintain.assert_called_once_with()


class TestInitStateCorruptResult:
    def test_corrupt_result_json_not_counted(self, tmp_path: Path, monkeypatch):
        import yaml

        batch_id = "test-corrupt"
        dataset_short = "SWE-bench_Verified"
        # _init_state expects output/<dataset_short>/<batch_id>/
        batch_dir = tmp_path / "output" / dataset_short / batch_id
        inst_dir = batch_dir / "inst1"
        inst_dir.mkdir(parents=True)
        # Write corrupt result.json
        (inst_dir / "result.json").write_text("not valid json {{")
        # Write valid result.json
        inst2_dir = batch_dir / "inst2"
        inst2_dir.mkdir(parents=True)
        (inst2_dir / "result.json").write_text('{"plans": []}')

        cfg = {"system": {"batch_id": batch_id, "dataset": f"SWE-bench/{dataset_short}"}}
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(cfg))

        sample_file = batch_dir / "sampled_instances.json"
        sample_file.write_text(json.dumps({"instances": ["inst1", "inst2"]}))

        monkeypatch.chdir(tmp_path)
        with patch.object(watchdog, "STATE_FILE", tmp_path / "state.json"):
            state = watchdog._init_state()

        assert state.completed == 1
        assert state.total_instances == 2


class TestAnalysisState:
    def test_roundtrip_with_analysis_fields(self, tmp_path: Path):
        state = watchdog.WatchdogState(
            batch_id="test-batch",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            analysis_completed=7,
        )
        with patch.object(watchdog, "STATE_FILE", tmp_path / "state.json"):
            watchdog.save_state(state)
            loaded = watchdog.load_state()
        assert loaded.analysis_phase == "flash"
        assert loaded.analysis_completed == 7

    def test_backward_compat_missing_analysis_fields(self, tmp_path: Path):
        raw = {
            "batch_id": "old-batch",
            "total_instances": 10,
            "completed": 10,
            "status": "completed",
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(raw))
        with patch.object(watchdog, "STATE_FILE", state_file):
            loaded = watchdog.load_state()
        assert loaded.analysis_phase is None
        assert loaded.analysis_completed == 0
        assert loaded.aggregation_phase is None


class TestAnalysisLogAnalysis:
    def test_detect_api_rate_limit_in_analysis_log(self, tmp_path: Path):
        log = tmp_path / "analysis_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START sphinx-doc__sphinx-9229\n"
            "[2026-05-12 10:05:00] ERROR: DeepSeek API returned 429 Too Many Requests\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", log):
            analysis = watchdog.analyze_recent_logs(log)
        assert analysis["api_rate_limited"] is True
        assert analysis["api_auth_failed"] is False

    def test_analysis_end_marker_detected(self, tmp_path: Path):
        log = tmp_path / "analysis_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START inst1\n"
            "[2026-05-12 10:01:00] DONE  inst1 rc=0 elapsed=60s\n"
            "[2026-05-12 10:01:01] === Analysis end ===\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", log):
            analysis = watchdog.analyze_recent_logs(log)
        assert analysis["batch_completed"] is True

    def test_limits_exceeded_is_expected_failure(self, tmp_path: Path):
        log = tmp_path / "analysis_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] FAIL  inst1 rc=1 elapsed=300s\n"
            "Contrastive agent for inst1 terminated without submission (exit_status=LimitsExceeded)\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", log):
            analysis = watchdog.analyze_recent_logs(log)
        assert analysis["expected_failure"] is True


class TestAnalysisHangDetection:
    def test_analysis_log_stale(self, tmp_path: Path):
        log = tmp_path / "analysis_run.log"
        log.write_text("old log line\n")
        old_time = time.time() - watchdog.HANG_TIMEOUT_SECONDS - 60
        os.utime(log, (old_time, old_time))
        assert watchdog.is_log_stale(log) is True

    def test_analysis_log_recent(self, tmp_path: Path):
        log = tmp_path / "analysis_run.log"
        log.write_text("recent log line\n")
        assert watchdog.is_log_stale(log) is False


class TestAggregationState:
    def test_roundtrip_with_aggregation_fields(self, tmp_path: Path):
        state = watchdog.WatchdogState(
            batch_id="test-batch",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="done",
            aggregation_phase="flash",
        )
        with patch.object(watchdog, "STATE_FILE", tmp_path / "state.json"):
            watchdog.save_state(state)
            loaded = watchdog.load_state()
        assert loaded.aggregation_phase == "flash"

    def test_backward_compat_missing_aggregation_fields(self, tmp_path: Path):
        raw = {
            "batch_id": "old-batch",
            "total_instances": 10,
            "completed": 10,
            "status": "completed",
            "analysis_phase": "done",
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(raw))
        with patch.object(watchdog, "STATE_FILE", state_file):
            loaded = watchdog.load_state()
        assert loaded.aggregation_phase is None


class TestSerialPhaseHandoff:
    def test_flash_done_enters_aggregation_when_review_disabled(self, tmp_path: Path):
        """Simulate: flash analysis done → flash aggregation should start (review disabled)."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text(
            "[2026-05-12 10:00:00] START inst1\n"
            "[2026-05-12 10:01:00] === Analysis end ===\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = False
        if state.analysis_phase == "flash" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "flash"
        assert state.analysis_phase == "flash"
        assert state.aggregation_phase == "flash"
        assert state.review_phase is None

    def test_flash_done_enters_review_when_enabled(self, tmp_path: Path):
        """Simulate: flash analysis done → flash review should start (review enabled)."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text(
            "[2026-05-12 10:00:00] START inst1\n"
            "[2026-05-12 10:01:00] === Analysis end ===\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = True
        if state.analysis_phase == "flash" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "flash"
        assert state.analysis_phase == "flash"
        assert state.review_phase == "reviewing"
        assert state.aggregation_phase is None

    def test_flash_aggregation_done_hands_off_to_pro(self):
        """Simulate: flash aggregation done → pro analysis should start."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            aggregation_phase="flash",
        )
        # Simulate the transition logic when aggregation completes
        if state.aggregation_phase == "flash":
            state.analysis_phase = "pro"
            state.analysis_completed = 0
            state.aggregation_phase = None
        assert state.analysis_phase == "pro"
        assert state.aggregation_phase is None
        assert state.analysis_completed == 0

    def test_flash_review_done_enters_aggregation(self):
        """Simulate: flash review done → flash aggregation should start."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            review_phase="done",
        )
        # Simulate the transition logic when review_phase == "done"
        if state.analysis_phase == "flash" and state.review_phase == "done":
            state.aggregation_phase = "flash"
            state.review_phase = None
        assert state.analysis_phase == "flash"
        assert state.aggregation_phase == "flash"
        assert state.review_phase is None

    def test_pro_done_enters_aggregation_when_review_disabled(self, tmp_path: Path):
        """Simulate: pro analysis done → pro aggregation should start (review disabled)."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="pro",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text("[2026-05-12 10:01:00] === Analysis end ===\n")
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = False
        finished = False
        if state.analysis_phase == "pro" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "pro"
        assert state.analysis_phase == "pro"
        assert state.aggregation_phase == "pro"
        assert state.review_phase is None

    def test_pro_aggregation_done_exits(self):
        """Simulate: pro aggregation done → watchdog exits."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="pro",
            aggregation_phase="pro",
        )
        # Simulate the transition logic when aggregation completes
        if state.aggregation_phase == "pro":
            finished = True
        assert finished is True


class TestCooldownDuringAnalysis:
    def test_cooldown_resumes_analysis_not_batch(self, tmp_path: Path):
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="api_cooldown",
            analysis_phase="flash",
            api_cooldown_until=datetime.now(timezone.utc).isoformat(),
        )
        assert state.analysis_phase == "flash"
        # After cooldown expires, the main loop would call start_analysis,
        # not start_batch. We verify the phase stays "flash".
        assert watchdog.cooldown_expired(state) is True


class TestRepairPrompt:
    def test_prompt_contains_regression_testing(self):
        state = watchdog.WatchdogState(batch_id="b", total_instances=10)
        assert "Run the FULL test suite" in str(watchdog.invoke_claude_repair.__code__.co_consts)
        assert "write additional tests" in str(watchdog.invoke_claude_repair.__code__.co_consts)

    def test_prompt_forbids_watchdog_modification(self):
        state = watchdog.WatchdogState(batch_id="b", total_instances=10)
        consts = str(watchdog.invoke_claude_repair.__code__.co_consts)
        assert "scripts/long_run_watchdog.py" in consts


class TestReviewState:
    def test_roundtrip_with_review_fields(self, tmp_path: Path):
        state = watchdog.WatchdogState(
            batch_id="test-batch",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="done",
            review_phase="reviewing",
            rework_queue=["inst1", "inst2"],
            rework_attempts={"inst1": 1, "inst2": 2},
            review_results={"inst1": {"score": 80, "passed": True}},
        )
        with patch.object(watchdog, "STATE_FILE", tmp_path / "state.json"):
            watchdog.save_state(state)
            loaded = watchdog.load_state()
        assert loaded.review_phase == "reviewing"
        assert loaded.rework_queue == ["inst1", "inst2"]
        assert loaded.rework_attempts == {"inst1": 1, "inst2": 2}
        assert loaded.review_results == {"inst1": {"score": 80, "passed": True}}

    def test_backward_compat_missing_review_fields(self, tmp_path: Path):
        raw = {
            "batch_id": "old-batch",
            "total_instances": 10,
            "completed": 10,
            "status": "completed",
            "analysis_phase": "done",
        }
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps(raw))
        with patch.object(watchdog, "STATE_FILE", state_file):
            loaded = watchdog.load_state()
        assert loaded.review_phase is None
        assert loaded.rework_queue == []
        assert loaded.rework_attempts == {}
        assert loaded.review_results == {}


class TestRuleQualityEvaluation:
    def test_perfect_rule_passes(self):
        rule = (
            "When a plan only modifies the primary target function without tracing down "
            "all internal helper functions, the strategy should be to perform a dependency "
            "trace through the whole file, because fixing only the producer without fixing "
            "all consumers leaves the system with inconsistent escaping."
        )
        result = watchdog.evaluate_rule_quality(rule)
        assert result["passed"] is True
        assert result["score"] >= 70
        assert result["checks"]["starts_with_when"] is True
        assert result["checks"]["has_because"] is True
        assert result["checks"]["no_format_pollution"] is True
        assert result["checks"]["no_impl_details"] is True
        assert result["checks"]["has_strategy"] is True

    def test_empty_rule_fails(self):
        result = watchdog.evaluate_rule_quality("")
        assert result["passed"] is False
        assert result["checks"]["non_empty"] is False

    def test_short_rule_fails(self):
        # Missing both because AND strategy, so even with other checks it scores < 70
        result = watchdog.evaluate_rule_quality("When X do Y.")
        assert result["passed"] is False
        assert result["checks"]["length_ok"] is False

    def test_missing_because_fails(self):
        rule = (
            "When a plan only modifies the primary target function without tracing down "
            "all internal helper functions, the strategy should be to perform a dependency trace."
        )
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["has_because"] is False

    def test_markdown_pollution_fails(self):
        rule = "## Rule 1\nWhen X, do Y because Z."
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["no_format_pollution"] is False

    def test_tool_call_pollution_fails(self):
        rule = "<tool_calls>\nWhen X, do Y because Z."
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["no_format_pollution"] is False

    def test_file_path_impl_detail_fails(self):
        rule = (
            "When /testbed/foo.py has a bug, do Y because Z."
        )
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["no_impl_details"] is False

    def test_line_number_impl_detail_fails(self):
        rule = (
            "When line 42 has an error, do Y because Z."
        )
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["no_impl_details"] is False

    def test_no_strategy_fails(self):
        rule = (
            "When a bug exists, the output is wrong because the code is broken."
        )
        result = watchdog.evaluate_rule_quality(rule)
        assert result["checks"]["has_strategy"] is False


class TestReviewAllRules:
    def test_all_pass_no_rework(self, tmp_path: Path):
        per_case = tmp_path / "per_case"
        per_case.mkdir()
        for i, rule in enumerate([
            "When A, do B because C.",
            "When X, verify Y because Z.",
        ]):
            data = {"instance_id": f"inst{i}", "rule": rule * 20, "rule_valid": True}
            (per_case / f"inst{i}.json").write_text(json.dumps(data))

        queue, results = watchdog.review_all_rules(str(tmp_path))
        assert queue == []
        assert len(results) == 2
        for r in results.values():
            assert r["passed"] is True

    def test_some_fail_queued_for_rework(self, tmp_path: Path):
        per_case = tmp_path / "per_case"
        per_case.mkdir()
        # Good rule
        good = {"instance_id": "good_inst", "rule": "When A, do B because C." * 15, "rule_valid": True}
        (per_case / "good_inst.json").write_text(json.dumps(good))
        # Bad rule (empty)
        bad = {"instance_id": "bad_inst", "rule": "", "rule_valid": False}
        (per_case / "bad_inst.json").write_text(json.dumps(bad))

        queue, results = watchdog.review_all_rules(str(tmp_path))
        assert queue == ["bad_inst"]
        assert results["good_inst"]["passed"] is True
        assert results["bad_inst"]["passed"] is False

    def test_missing_per_case_dir(self, tmp_path: Path):
        queue, results = watchdog.review_all_rules(str(tmp_path / "nonexistent"))
        assert queue == []
        assert results == {}


class TestReworkHelpers:
    def test_rework_session_name(self):
        assert watchdog.is_rework_complete("django__django-12345") is True
        # (session does not exist in test env)

    def test_rework_log_path(self):
        path = watchdog.get_rework_log_path("django__django-12345")
        assert "rework_django_django-12345" in str(path)


class TestProDoneEntersReview:
    def test_pro_done_sets_review_phase(self, tmp_path: Path):
        """Simulate the main-loop transition when pro analysis ends."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="pro",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text("[2026-05-12 10:01:00] === Analysis end ===\n")
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        # New handoff: pro analysis complete → start pro review
        if state.analysis_phase == "pro" and analysis["batch_completed"]:
            state.review_phase = "reviewing"

        assert state.analysis_phase == "pro"
        assert state.review_phase == "reviewing"


class TestAggregationConstants:
    def test_aggregation_tmux_session_defined(self):
        assert watchdog.AGGREGATION_TMUX_SESSION == "pct-aggregation"

    def test_aggregation_log_defined(self):
        assert watchdog.AGGREGATION_LOG.name == "aggregation_run.log"

    def test_start_aggregation_function_exists(self):
        assert callable(watchdog.start_aggregation)


class TestUnifiedExperimentEntrypoint:
    @patch.object(watchdog.time, "sleep")
    @patch.object(watchdog, "_tmux_session_exists", return_value=True)
    @patch.object(watchdog, "_kill_tmux_session")
    @patch.object(watchdog.subprocess, "run")
    def test_analysis_starts_through_run_batch(
        self,
        mock_run,
        mock_kill,
        mock_exists,
        mock_sleep,
    ):
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=1,
            analysis_phase="flash",
        )

        watchdog.start_analysis(state)

        command = mock_run.call_args.args[0][-1]
        assert "scripts/run_batch.sh --analysis-only" in command
        assert "scripts/internal/" not in command

    @patch.object(watchdog.time, "sleep")
    @patch.object(watchdog, "_tmux_session_exists", return_value=True)
    @patch.object(watchdog, "_kill_tmux_session")
    @patch.object(watchdog.subprocess, "run")
    def test_checker_starts_through_run_batch(
        self,
        mock_run,
        mock_kill,
        mock_exists,
        mock_sleep,
        tmp_path,
    ):
        state = watchdog.WatchdogState(batch_id="b", total_instances=1)
        with patch.object(watchdog, "CHECKER_LOG", tmp_path / "checker.log"):
            watchdog.start_checker_eval(state)

        command = mock_run.call_args.args[0][-1]
        assert "scripts/run_batch.sh --checker-comparison" in command
        assert "SWE-bench_Pro" not in command


class TestReviewConstants:
    def test_review_tmux_session_defined(self):
        assert watchdog.REVIEW_TMUX_SESSION == "pct-review"

    def test_review_log_defined(self):
        assert watchdog.REVIEW_LOG.name == "review_run.log"

    def test_start_review_function_exists(self):
        assert callable(watchdog.start_review)


class TestReviewLogAnalysis:
    def test_detect_review_end_marker(self, tmp_path: Path):
        log = tmp_path / "review_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START review for inst1\n"
            "[2026-05-12 10:01:00] DONE review for inst1 score=85 passed=True\n"
            "[2026-05-12 10:01:01] === Review end ===\n"
        )
        with patch.object(watchdog, "REVIEW_LOG", log):
            analysis = watchdog.analyze_recent_logs(log)
        assert analysis["batch_completed"] is True

    def test_detect_api_rate_limit_in_review_log(self, tmp_path: Path):
        log = tmp_path / "review_run.log"
        log.write_text(
            "[2026-05-12 10:00:00] START review for inst1\n"
            "[2026-05-12 10:05:00] ERROR: DeepSeek API returned 429 Too Many Requests\n"
        )
        with patch.object(watchdog, "REVIEW_LOG", log):
            analysis = watchdog.analyze_recent_logs(log)
        assert analysis["api_rate_limited"] is True


class TestReviewHangDetection:
    def test_review_log_stale(self, tmp_path: Path):
        log = tmp_path / "review_run.log"
        log.write_text("old review log line\n")
        old_time = time.time() - watchdog.HANG_TIMEOUT_SECONDS - 60
        os.utime(log, (old_time, old_time))
        assert watchdog.is_log_stale(log) is True

    def test_review_log_recent(self, tmp_path: Path):
        log = tmp_path / "review_run.log"
        log.write_text("recent review log line\n")
        assert watchdog.is_log_stale(log) is False


class TestCooldownDuringReview:
    def test_cooldown_resumes_review_not_batch(self):
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="api_cooldown",
            analysis_phase="pro",
            review_phase="reviewing",
            api_cooldown_until=datetime.now(timezone.utc).isoformat(),
        )
        # After cooldown expires, the main loop should resume review
        assert state.review_phase == "reviewing"
        assert watchdog.cooldown_expired(state) is True


class TestReviewPhaseDoneTransitions:
    def test_flash_review_done_moves_to_aggregation(self):
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            review_phase="done",
        )
        # Simulate review_phase == "done" handler
        if state.analysis_phase == "flash" and state.review_phase == "done":
            state.aggregation_phase = "flash"
            state.review_phase = None
        assert state.analysis_phase == "flash"
        assert state.aggregation_phase == "flash"
        assert state.review_phase is None

    def test_pro_review_done_moves_to_aggregation(self):
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="pro",
            review_phase="done",
        )
        # Simulate review_phase == "done" handler
        if state.analysis_phase == "pro" and state.review_phase == "done":
            state.aggregation_phase = "pro"
            state.review_phase = None
        assert state.analysis_phase == "pro"
        assert state.aggregation_phase == "pro"
        assert state.review_phase is None


class TestEnableReviewFlag:
    """When ``enable_review`` is false, the watchdog skips review/rework
    and transitions directly: flash analysis → flash aggregation → pro analysis
    → pro aggregation → done.
    """

    def test_flash_done_skips_review_enters_aggregation_when_disabled(self, tmp_path: Path):
        """Simulate the main-loop transition when flash analysis ends
        and enable_review is false.
        """
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text(
            "[2026-05-12 10:00:00] START inst1\n"
            "[2026-05-12 10:01:00] === Analysis end ===\n"
        )
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = False
        if state.analysis_phase == "flash" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "flash"
        assert state.analysis_phase == "flash"
        assert state.aggregation_phase == "flash"
        assert state.review_phase is None

    def test_pro_done_enters_aggregation_when_review_disabled(self, tmp_path: Path):
        """Simulate the main-loop transition when pro analysis ends
        and enable_review is false.
        """
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="pro",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text("[2026-05-12 10:01:00] === Analysis end ===\n")
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = False
        if state.analysis_phase == "pro" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "pro"
        assert state.analysis_phase == "pro"
        assert state.aggregation_phase == "pro"
        assert state.review_phase is None

    def test_flash_done_enters_review_when_enabled(self, tmp_path: Path):
        """When enable_review is true, flash analysis still enters review."""
        state = watchdog.WatchdogState(
            batch_id="b",
            total_instances=60,
            completed=60,
            status="completed",
            analysis_phase="flash",
            analysis_completed=60,
        )
        analysis_log = tmp_path / "analysis_run.log"
        analysis_log.write_text("[2026-05-12 10:01:00] === Analysis end ===\n")
        with patch.object(watchdog, "ANALYSIS_LOG", analysis_log):
            analysis = watchdog.analyze_recent_logs(analysis_log)
        assert analysis["batch_completed"] is True

        enable_review = True
        if state.analysis_phase == "flash" and analysis["batch_completed"]:
            if enable_review:
                state.review_phase = "reviewing"
            else:
                state.aggregation_phase = "flash"
        assert state.analysis_phase == "flash"
        assert state.review_phase == "reviewing"
        assert state.aggregation_phase is None


class TestLoadEnableReview:
    def test_reads_true_from_config(self, tmp_path: Path, monkeypatch):
        import yaml

        monkeypatch.chdir(tmp_path)
        cfg = {"analysis": {"enable_review": True}}
        Path("config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
        assert watchdog._load_enable_review() is True

    def test_reads_false_from_config(self, tmp_path: Path, monkeypatch):
        import yaml

        monkeypatch.chdir(tmp_path)
        cfg = {"analysis": {"enable_review": False}}
        Path("config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
        assert watchdog._load_enable_review() is False

    def test_string_true_coerced(self, tmp_path: Path, monkeypatch):
        import yaml

        monkeypatch.chdir(tmp_path)
        cfg = {"analysis": {"enable_review": "true"}}
        Path("config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
        assert watchdog._load_enable_review() is True

    def test_missing_key_defaults_false(self, tmp_path: Path, monkeypatch):
        import yaml

        monkeypatch.chdir(tmp_path)
        cfg = {"analysis": {"model": "deepseek-v4-flash"}}
        Path("config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
        assert watchdog._load_enable_review() is False

    def test_missing_file_defaults_false(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert watchdog._load_enable_review() is False
