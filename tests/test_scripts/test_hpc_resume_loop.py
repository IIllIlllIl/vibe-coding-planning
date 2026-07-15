from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "hpc_supervisor_service.py"


def _write_config(root: Path) -> Path:
    snapshot = root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    rules = root / "rules.md"
    rules.write_text("1. rule\n", encoding="utf-8")
    run_dir = root / "run"
    config = root / "gepa.yaml"
    config.write_text(
        f"""
paths:
  dataset_snapshot: {snapshot}
  initial_rules: {rules}
  run_dir: {run_dir}
checker:
  model: deepseek-v4-flash
reflection:
  model: deepseek-v4-flash
search:
  max_metric_calls: 1
container:
  runtime: apptainer
  sif_cache_dir: /scratch/test/sif-cache
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )
    return config


def _fake_batch_script(path: Path, log_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> {log_path}
if [[ " $* " == *" --submit "* ]]; then
  count_file="{log_path}.count"
  count=0
  [[ -f "$count_file" ]] && count=$(cat "$count_file")
  count=$((count + 1))
  printf '%s' "$count" > "$count_file"
  printf '{{"job_id":"job-%s"}}\\n' "$count"
else
  printf '%s\\n' "$@"
fi
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _fake_ssh(path: Path, status_sequence: Path, log_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
cmd="${{@: -1}}"
printf '%s\\n' "$cmd" >> {log_path}
if [[ "$cmd" == *"VIBE_HPC_RUN_STATUS"* ]]; then
  line=$(head -n 1 {status_sequence})
  tail -n +2 {status_sequence} > {status_sequence}.tmp
  mv {status_sequence}.tmp {status_sequence}
  printf '%s\\n' "$line"
elif [[ "$cmd" == squeue* ]]; then
  exit 0
elif [[ "$cmd" == sacct* ]]; then
  printf 'COMPLETED|0:0\\n'
else
  exit 1
fi
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _env(fake_bin: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["ULHPC_USER"] = "tester"
    env["ULHPC_HOST"] = "example.invalid"
    env["ULHPC_PORT"] = "2222"
    return env


def test_hpc_resume_loop_dry_run_delegates_slice_time(tmp_path: Path) -> None:
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_dry"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:30:00",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--job-name",
            "dry-test",
            "--time",
            "24:00:00",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    assert "--time\n00:30:00" in batch_log.read_text(encoding="utf-8")
    assert "24:00:00" not in batch_log.read_text(encoding="utf-8")
    assert "--dry-run" in batch_log.read_text(encoding="utf-8")
    assert "--submit" not in batch_log.read_text(encoding="utf-8")


def test_hpc_resume_loop_resubmits_until_completed(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_submit"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    ssh_log = tmp_path / "ssh.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        "\n".join(
            [
                '{"state":"missing"}',
                '{"state":"resumable"}',
                '{"state":"result","status":"completed"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, ssh_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:10:00",
            "--check-interval",
            "0",
            "--poll-interval",
            "0",
            "--max-runs",
            "3",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--job-name",
            "resume-test",
            "--remote-run-dir",
            "~/remote-run",
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("[hpc-resume] submitted job_id=") == 2
    batch_text = batch_log.read_text(encoding="utf-8")
    assert batch_text.count("--submit") == 2
    assert batch_text.count("00:10:00") == 2
    assert "~/remote-run/.tmp_hpc_smoke/test_hpc_resume_loop_submit/run" in (
        ssh_log.read_text(encoding="utf-8")
    )


def test_hpc_resume_loop_stops_on_failed_result(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_resume_loop_failed"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"result","status":"failed"}\n', encoding="utf-8")
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--check-interval",
            "0",
            "--poll-interval",
            "0",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 1
    assert "run is blocked; not resubmitting" in result.stderr
    assert not batch_log.exists()


def test_hpc_supervisor_retries_status_check_failure_without_submitting(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_status_failure"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)
    (fake_bin / "ssh").write_text("#!/usr/bin/env bash\nexit 255\n", encoding="utf-8")
    (fake_bin / "ssh").chmod(0o755)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0
    assert "retrying without submission" in result.stderr
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "waiting_after_status_check_failure"


def test_hpc_supervisor_retains_loop_after_submission_failure(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_submission_failure"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    fake_batch.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_batch.chmod(0o755)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0
    assert "retrying later" in result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "waiting_after_submission_failure"
    assert state["submissions"] == 0


def test_hpc_supervisor_service_starts_with_tmux_and_caffeinate(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        "if [[ \"$1\" == has-session ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--session",
            "online-gepa-test",
            "--log",
            str(tmp_path / "supervisor.log"),
            "--poll-interval",
            "1800",
            "--gepa-config",
            "configs/gepa_online_planning_pilot.yaml",
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "new-session -d -s online-gepa-test" in invocation
    assert "exec caffeinate -i -s conda run -n mini-swe python" in invocation
    assert "hpc_resume_loop.py --poll-interval 1800" in invocation


def test_hpc_supervisor_service_uses_persisted_launch_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        "if [[ \"$1\" == has-session ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    launch_config = tmp_path / "launch.yaml"
    launch_config.write_text(
        """
schema_version: 1
session: persisted-online-gepa
log: .local/hpc-supervisor/persisted.log
arguments:
  - --target-iterations
  - "8"
  - --gepa-config
  - configs/gepa_online_planning_hpc.yaml
  - --submit
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "python",
            str(SERVICE_SCRIPT),
            "start",
            "--launch-config",
            str(launch_config),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "new-session -d -s persisted-online-gepa" in invocation
    assert "hpc_resume_loop.py --target-iterations 8" in invocation
    assert "configs/gepa_online_planning_hpc.yaml --submit" in invocation


def test_hpc_supervisor_waits_for_workers_without_submitting(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_wait"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"resumable","completed_iterations":2,'
        '"first_observed_completed_iterations":2,'
        '"active_controllers":[],"active_workers":["9001"]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "3",
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "waiting without submission" in result.stdout
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["baseline_iterations"] == 2
    assert state["target_completed_iterations"] == 5


def test_hpc_supervisor_stops_at_additional_iteration_target(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_target"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"resumable","completed_iterations":5,'
        '"first_observed_completed_iterations":2,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "submissions": 1,
                "remote_run_snapshot": (
                    "~/hpc_run_state/vibe-coding-planning/"
                    ".tmp_hpc_smoke/test_hpc_supervisor_target/run"
                ),
                "job_name": "vibe-gepa",
                "target_additional_iterations": 3,
                "baseline_iterations": 2,
                "target_completed_iterations": 5,
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "3",
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 0, result.stderr
    assert "iteration target reached" in result.stdout
    assert not batch_log.exists()


def test_hpc_supervisor_rejects_completion_before_iteration_target(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_supervisor_short"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":"completed","completed_iterations":3,'
        '"first_observed_completed_iterations":0,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "8",
            "--once",
            "--state-file",
            str(tmp_path / "state.json"),
            "--batch-script",
            str(fake_batch),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--submit",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(fake_bin),
    )

    assert result.returncode == 2
    assert "completed before iteration target" in result.stderr
    assert not batch_log.exists()
