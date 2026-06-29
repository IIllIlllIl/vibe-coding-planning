from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.hpc_preheat_watchdog_lib import classifier, cli, repair, supervisor
from scripts.hpc_preheat_watchdog_lib.config import WatchdogConfig, parse_command
from scripts.hpc_preheat_watchdog_lib.preheat import SubmittedJob
from scripts.hpc_preheat_watchdog_lib.state import WatchdogState, load_state, save_state


class FakeSlurm:
    def __init__(self, *, state: str = "PENDING", complete: bool = False):
        self.state = state
        self.complete = complete
        self.state_calls = 0
        self.log_calls = 0
        self.cache_calls = 0

    def get_job_state(self, job_id: str) -> str:
        self.state_calls += 1
        return self.state

    def read_logs(self, stdout_path: str | None, stderr_path: str | None) -> str:
        self.log_calls += 1
        return "pilot logs"

    def cache_status(self, cache_dir: str, expected: int) -> dict:
        self.cache_calls += 1
        return {"complete": self.complete, "sif_count": expected if self.complete else 0}


def _config(tmp_path: Path, *, submit: bool = True, enable_agent_repair: bool = False) -> WatchdogConfig:
    pilot = tmp_path / "pilot.yaml"
    full = tmp_path / "full.yaml"
    pilot.write_text("paths: {}\n", encoding="utf-8")
    full.write_text("paths: {}\n", encoding="utf-8")
    return WatchdogConfig(
        pilot_config=pilot,
        full_config=full,
        pilot_sif_cache_dir="/scratch/pilot-cache",
        full_sif_cache_dir="/scratch/full-cache",
        state_file=tmp_path / "state.json",
        preheat_script=tmp_path / "submit.sh",
        ulhpc_config=tmp_path / "ulhpc.yaml",
        submit=submit,
        enable_agent_repair=enable_agent_repair,
    )


def test_cli_defaults_poll_interval_to_60_minutes() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--pilot-config",
            "pilot.yaml",
            "--full-config",
            "full.yaml",
            "--pilot-sif-cache-dir",
            "/scratch/pilot",
            "--full-sif-cache-dir",
            "/scratch/full",
        ]
    )

    assert args.poll_interval == 3600
    assert args.pull_timeout == "0"
    assert args.max_pull_attempts == "1"
    assert args.retry_backoff == "0"
    assert args.agent_cooldown == 18000
    assert args.max_agent_cooldowns == 20


def test_parse_command_uses_shell_quoting() -> None:
    assert parse_command('codex exec --sandbox workspace-write -C "/tmp/my repo"') == (
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-C",
        "/tmp/my repo",
    )


def test_cli_accepts_custom_agent_command() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--pilot-config",
            "pilot.yaml",
            "--full-config",
            "full.yaml",
            "--pilot-sif-cache-dir",
            "/scratch/pilot",
            "--full-sif-cache-dir",
            "/scratch/full",
            "--agent-command",
            "codex exec --sandbox workspace-write --ask-for-approval never",
        ]
    )
    config = cli.config_from_args(args)

    assert config.agent_command == (
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
    )


def test_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WatchdogState(
        phase="pilot_waiting",
        pilot_job_id="123",
        expected_pilot_images=5,
        last_error_class="module_python",
    )

    save_state(path, state)
    loaded = load_state(path)

    assert loaded.phase == "pilot_waiting"
    assert loaded.pilot_job_id == "123"
    assert loaded.expected_pilot_images == 5
    assert loaded.last_error_class == "module_python"


@pytest.mark.parametrize(
    ("text", "error_class", "repairable"),
    [
        ("bash: syntax error near unexpected token", "shell_syntax", True),
        ("ulhpc-submit: unknown option --bad", "ulhpc_submit_args", True),
        ("dataset snapshot missing after staging", "dataset_staging", True),
        ("ValueError: environment variable DEEPSEEK_API_KEY is not set", "config_key_coupling", True),
        ("slurmstepd: error: job cancelled DUE TO TIME LIMIT", "time_limit", True),
        ("Permission denied (publickey)", "ssh_auth", True),
        ("QOSMaxWallDurationPerJobLimit", "slurm_qos", True),
        ("No space left on device", "disk_full", True),
        ("TLS handshake timeout", "registry_network", True),
        ("DEEPSEEK_API_KEY leaked", "secret_related", False),
        ("unrecognized fatal condition", "unknown", True),
    ],
)
def test_classifier(text: str, error_class: str, repairable: bool) -> None:
    result = classifier.classify_failure(text)

    assert result.error_class == error_class
    assert result.repairable is repairable


def test_supervisor_does_not_resubmit_active_pilot(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = WatchdogState(phase="pilot_waiting", pilot_job_id="job-1")
    submitted: list[str] = []
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 3)
    monkeypatch.setattr(
        supervisor,
        "submit_preheat",
        lambda config, role: submitted.append(role) or SubmittedJob(job_id="new"),
    )

    result = supervisor.run_once(config, state, FakeSlurm(state="PENDING"))

    assert result.phase == "pilot_waiting"
    assert result.pilot_job_id == "job-1"
    assert submitted == []


def test_supervisor_submits_full_after_pilot_success(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = WatchdogState(
        phase="pilot_waiting",
        pilot_job_id="pilot-1",
        pilot_stdout_path="/remote/out",
        pilot_stderr_path="/remote/err",
    )
    submitted: list[str] = []

    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 2)

    def fake_submit(config: WatchdogConfig, *, role: str) -> SubmittedJob:
        submitted.append(role)
        return SubmittedJob(
            job_id=f"{role}-job",
            stdout_path=f"/remote/{role}.out",
            stderr_path=f"/remote/{role}.err",
        )

    monkeypatch.setattr(supervisor, "submit_preheat", fake_submit)
    slurm = FakeSlurm(state="COMPLETED", complete=True)

    result = supervisor.run_once(config, state, slurm)
    assert result.phase == "pilot_completed"
    assert result.last_sif_count == 2

    result = supervisor.run_once(config, result, slurm)
    assert result.phase == "full_preheat_submitted"
    assert result.full_job_id == "full-job"
    assert submitted == ["full"]


def test_supervisor_routes_full_job_failure_to_repair_path(monkeypatch, tmp_path: Path) -> None:
    config = replace(_config(tmp_path, enable_agent_repair=True), stop_after_full_submit=False)
    state = WatchdogState(
        phase="full_preheat_submitted",
        full_job_id="full-1",
        full_stdout_path="/remote/full.out",
        full_stderr_path="/remote/full.err",
    )
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 2)
    monkeypatch.setattr(
        supervisor,
        "run_agent_repair",
        lambda config, error_class, logs: repair.RepairResult(ok=True, output="ok"),
    )
    monkeypatch.setattr(supervisor, "changed_files", lambda: [])
    monkeypatch.setattr(supervisor, "run_validations", lambda: SimpleNamespace(ok=True, output="ok"))

    result = supervisor.run_once(config, state, FakeSlurm(state="FAILED"))
    assert result.phase == "pilot_failed"
    assert result.last_error == "pilot logs"

    result = supervisor.run_once(config, result, FakeSlurm())
    assert result.phase == "idle"
    assert result.repair_attempts == 1


def test_validation_failure_retries_until_repair_budget(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, enable_agent_repair=True)
    state = WatchdogState(phase="pilot_failed", last_error="syntax error")
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 1)
    monkeypatch.setattr(supervisor, "changed_files", lambda: [])
    monkeypatch.setattr(
        supervisor,
        "run_agent_repair",
        lambda config, error_class, logs: repair.RepairResult(ok=True, output="ok"),
    )
    monkeypatch.setattr(
        supervisor,
        "run_validations",
        lambda: SimpleNamespace(ok=False, output="validation failed"),
    )

    result = supervisor.run_once(config, state, FakeSlurm())

    assert result.phase == "pilot_failed"
    assert result.last_error_class == "repair_validation"
    assert result.repair_attempts == 1


def test_agent_quota_enters_five_hour_cooldown(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, enable_agent_repair=True)
    state = WatchdogState(phase="pilot_failed", last_error="usage limit exceeded")
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 1)

    result = supervisor.run_once(config, state, FakeSlurm())

    assert result.phase == "agent_cooldown"
    assert result.agent_cooldowns == 1
    assert result.cooldown_until is not None


def test_agent_repair_uses_configured_agent_command(monkeypatch, tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        agent_command=("codex", "exec", "--sandbox", "workspace-write"),
    )
    captured: list[list[str]] = []

    def fake_run_command(command: list[str]):
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(repair, "run_command", fake_run_command)

    result = repair.run_agent_repair(config, error_class="time_limit", logs="DUE TO TIME LIMIT")

    assert result.ok is True
    assert captured
    assert captured[0][:4] == ["codex", "exec", "--sandbox", "workspace-write"]
    assert "Failure class: time_limit" in captured[0][-1]


def test_whitelist_violation_restores_and_retries(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, enable_agent_repair=True)
    state = WatchdogState(phase="pilot_failed", last_error="syntax error")
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 1)
    snapshots = iter(
        [
            set(),
            {"docs/hpc-submit.md"},
            set(),
        ]
    )
    restored: list[str] = []
    monkeypatch.setattr(supervisor, "changed_files", lambda: next(snapshots))
    monkeypatch.setattr(
        supervisor,
        "run_agent_repair",
        lambda config, error_class, logs: repair.RepairResult(ok=True, output="ok"),
    )
    monkeypatch.setattr(
        supervisor,
        "restore_new_disallowed_changes",
        lambda violations: restored.extend(item.path for item in violations),
    )

    result = supervisor.run_once(config, state, FakeSlurm())

    assert result.phase == "pilot_failed"
    assert result.whitelist_violations == 1
    assert restored == ["docs/hpc-submit.md"]


def test_preexisting_disallowed_change_blocks(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, enable_agent_repair=True)
    state = WatchdogState(phase="pilot_failed", last_error="syntax error")
    monkeypatch.setattr(supervisor, "expected_image_count", lambda path: 1)
    snapshots = iter(
        [
            {"docs/hpc-submit.md"},
            {"docs/hpc-submit.md"},
        ]
    )
    monkeypatch.setattr(supervisor, "changed_files", lambda: next(snapshots))
    monkeypatch.setattr(
        supervisor,
        "run_agent_repair",
        lambda config, error_class, logs: repair.RepairResult(ok=True, output="ok"),
    )
    monkeypatch.setattr(supervisor, "restore_new_disallowed_changes", lambda violations: None)

    result = supervisor.run_once(config, state, FakeSlurm())

    assert result.phase == "blocked"


def test_state_file_json_is_stable(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, WatchdogState(phase="blocked", last_error="x"))

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["phase"] == "blocked"
    assert payload["last_error"] == "x"
