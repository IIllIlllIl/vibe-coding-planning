from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from scripts.hpc_resume_loop import _remote_run_snapshot, _repo_relative


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "hpc_supervisor_service.py"


def test_repo_relative_preserves_worktree_local_symlink(tmp_path: Path) -> None:
    linked_target = tmp_path / "shared-output"
    linked_target.mkdir()
    link = REPO_ROOT / ".tmp_hpc_smoke" / "linked-output"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(linked_target, target_is_directory=True)

    assert _repo_relative(link / "run", "run_dir") == (
        ".tmp_hpc_smoke/linked-output/run"
    )


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


def _write_workflow_config(root: Path) -> Path:
    run_dir = root / "run"
    config = root / "workflow.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"mode: polybench_pcce\npaths:\n  run_dir: {run_dir}\n",
        encoding="utf-8",
    )
    return config


def test_evaluator_repair_supervisor_monitors_repair_root() -> None:
    config = (
        REPO_ROOT
        / "configs/archive/polybench_pcce/polybench_pcce_hpc_formal_seed.yaml"
    )
    snapshot = _remote_run_snapshot(["--resume-evaluator", "native-home"], config)
    assert snapshot.endswith(
        "/output/SWE-PolyBench/polybench-pcce-runs/formal/"
        "seed-python111-20260817/evaluator_repairs/native-home"
    )


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
elif [[ "$cmd" == *"VIBE_OFFLINE_TARGET_EXTENSION"* ]]; then
  printf '{{"from":8,"to":20,"additional_iterations":12}}\\n'
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


def _config_identity(config: Path) -> dict[str, str]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "runtime_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "repo_commit": commit,
    }


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


def test_hpc_resume_loop_accepts_non_gepa_workflow_config(tmp_path: Path) -> None:
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_workflow_resume_dry"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_workflow.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--slice-time",
            "00:10:00",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--config",
            str(config),
            "--job-name",
            "workflow-dry-test",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_env(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    batch_text = batch_log.read_text(encoding="utf-8")
    assert f"--config\n{config}" in batch_text
    assert "--time\n00:10:00" in batch_text
    assert "--dry-run" in batch_text


def test_hpc_resume_loop_treats_completed_with_incomplete_as_terminal(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_workflow_terminal"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_workflow.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":"completed_with_incomplete"}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--poll-interval",
            "0",
            "--batch-script",
            str(fake_batch),
            "--state-file",
            str(tmp_path / "state.json"),
            "--config",
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
    assert not batch_log.exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"


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


def test_hpc_resume_loop_allows_one_named_controller_failure_recovery(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_controller_recovery"
    config = _write_workflow_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"controller_failed","controller_status":"failed",'
        '"error_type":"TaskBatchBlocked"}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_file = tmp_path / "state.json"

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--once",
            "--poll-interval",
            "0",
            "--state-file",
            str(state_file),
            "--recover-controller-error-type-once",
            "TaskBatchBlocked",
            "--batch-script",
            str(fake_batch),
            "--config",
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
    assert "allowing one audited Controller recovery" in result.stdout
    assert batch_log.is_file()
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["controller_failure_recoveries"] == 1
    assert state["status"] == "controller_submitted"


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


def test_hpc_supervisor_blocks_when_runtime_config_changes(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_config_identity"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    _fake_batch_script(fake_batch, batch_log)
    statuses = tmp_path / "statuses.txt"
    statuses.write_text('{"state":"missing"}\n', encoding="utf-8")
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")

    original = config.read_text(encoding="utf-8")
    process = subprocess.Popen(
        [
            "python",
            str(SCRIPT),
            "--poll-interval",
            "1",
            "--max-runs",
            "2",
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(fake_bin),
    )
    for _ in range(100):
        if batch_log.exists():
            break
        time.sleep(0.02)
    config.write_text(original + "\n# changed\n", encoding="utf-8")
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 1, (stdout, stderr)
    assert batch_log.read_text(encoding="utf-8").count("--submit") == 1
    assert "runtime config changed after supervisor start" in stderr
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "blocked_identity_mismatch"


def test_hpc_supervisor_service_starts_with_tmux_and_caffeinate(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "configs/archive/online_gepa/gepa_online_planning_pilot.yaml",
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
    assert (
        "exec caffeinate -i -s conda run --no-capture-output -n mini-swe python"
        in invocation
    )
    assert "hpc_resume_loop.py --poll-interval 1800" in invocation


def test_hpc_supervisor_service_uses_persisted_launch_config(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
  - configs/archive/online_gepa/gepa_online_planning_hpc.yaml
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
    assert "configs/archive/online_gepa/gepa_online_planning_hpc.yaml --submit" in invocation
    assert "conda run --no-capture-output -n mini-swe" in invocation


def test_pcce_supervisor_launch_config_uses_shared_resume_loop(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "--launch-config",
            "configs/polybench_pcce_supervisor_smoke.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "hpc_resume_loop.py --poll-interval 600" in invocation
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert "--config configs/polybench_pcce_hpc_smoke.yaml" in invocation


def test_formal_pcce_supervisor_launch_config_uses_formal_seed_runtime(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "--launch-config",
            "configs/polybench_pcce_supervisor_formal_seed_clean_20260826.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "hpc_resume_loop.py --poll-interval 600" in invocation
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--config configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml"
        in invocation
    )
    assert "--require-clean-worktree" in invocation


def test_c4_checker_only_supervisor_uses_pcce_resume_loop(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "--launch-config",
            "configs/polybench_pc_checker_only_c4_balanced20_supervisor_v1_20260831.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "caffeinate -i -s" in invocation
    assert (
        "--state-file .local/hpc-supervisor/"
        "polybench-pc-c4-balanced20-v1-20260831.json" in invocation
    )
    assert "--batch-script scripts/hpc_submit_polybench_pcce.sh" in invocation
    assert (
        "--ulhpc-config /Users/taoran.wang/.config/ulhpc-submit/config.yaml"
        in invocation
    )
    assert (
        "--config configs/polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml"
        in invocation
    )
    assert "--require-clean-worktree" in invocation
    assert "--submit" in invocation


def test_clean_seed_pcce_dependency_repair_supervisor_selects_frozen_subset(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "--launch-config",
            "configs/"
            "polybench_pcce_supervisor_formal_seed_dependency_cache_clean_20260826.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "--resume-evaluator clean-depcache-v1-20260826" in invocation
    assert (
        "--config configs/"
        "polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml"
        in invocation
    )
    assert (
        "--resume-evaluator-instances-file configs/frozen_dependency_caches/"
        "polybench_evaluator_dependencies_formal_v2_20260823/"
        "evaluator_repair_subset_clean99.json" in invocation
    )
    assert "--require-clean-worktree" in invocation


def test_formal_pcce_contract_retry_uses_new_supervisor_state_only(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux_log = tmp_path / "tmux.log"
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {tmux_log}\n"
        'if [[ "$1" == has-session ]]; then exit 1; fi\n'
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
            "--launch-config",
            "configs/archive/polybench_pcce/"
            "polybench_pcce_supervisor_formal_seed_contract_retry_20260818.yaml",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    invocation = tmux_log.read_text(encoding="utf-8")
    assert "--config configs/polybench_pcce_hpc_formal_seed.yaml" in invocation
    assert "--job-name polybench-pcce-seed-formal-20260817" in invocation
    assert (
        "--state-file .local/hpc-supervisor/"
        "polybench-pcce-seed-formal-contract-retry-submit-20260818.json" in invocation
    )
    assert "--recover-controller-error-type-once TaskBatchBlocked" in invocation
    assert "--require-clean-worktree" in invocation


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
    assert state["target_iterations"] == 3
    assert state["last_completed_iterations"] == 2


def test_hpc_supervisor_stops_at_cumulative_iteration_target(tmp_path: Path) -> None:
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
                **_config_identity(config),
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "5",
            "--once",
            "--state-file",
            str(state_path),
            "--batch-script",
            str(fake_batch),
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
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["target_iterations"] == 5


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
    assert "completed before cumulative iteration target" in result.stderr
    assert not batch_log.exists()


def test_hpc_supervisor_accepts_offline_controller_completion_status(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_offline_completion"
    config = _write_config(local_root)
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        '{"state":"result","status":null,"controller_status":"completed",'
        '"completed_iterations":2,"first_observed_completed_iterations":0,'
        '"active_controllers":[],"active_workers":[]}\n',
        encoding="utf-8",
    )
    _fake_batch_script(fake_batch, batch_log)
    _fake_ssh(fake_bin / "ssh", statuses, tmp_path / "ssh.log")
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "submissions": 1,
                "remote_run_snapshot": (
                    "~/hpc_run_state/vibe-coding-planning/"
                    ".tmp_hpc_smoke/test_offline_completion/run"
                ),
                "job_name": "vibe-gepa",
                "target_iterations": 2,
                **_config_identity(config),
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "python",
            str(SCRIPT),
            "--target-iterations",
            "2",
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
    assert not batch_log.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_completed_iterations"] == 2
    assert state["status"] == "completed"


def test_offline_supervisor_natively_extends_completed_target_and_resumes(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_native_offline_extension"
    config = _write_config(local_root)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "  max_metric_calls: 1\n",
            "  max_metric_calls: 1\n  max_iterations: 20\n",
        ),
        encoding="utf-8",
    )
    fake_batch = tmp_path / "hpc_submit_batch.sh"
    batch_log = tmp_path / "batch.log"
    ssh_log = tmp_path / "ssh.log"
    statuses = tmp_path / "statuses.txt"
    statuses.write_text(
        "\n".join(
            [
                '{"state":"result","status":"completed",'
                '"completed_iterations":8,"first_observed_completed_iterations":0,'
                '"active_controllers":[],"active_workers":[]}',
                '{"state":"resumable","completed_iterations":8,'
                '"first_observed_completed_iterations":0,'
                '"active_controllers":[],"active_workers":[]}',
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

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "extended completed Offline target from=8 to=20" in result.stdout
    assert "VIBE_OFFLINE_TARGET_EXTENSION" in ssh_log.read_text(encoding="utf-8")
    assert batch_log.read_text(encoding="utf-8").count("--submit") == 1
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["target_iterations"] == 20
    assert state["last_iteration_target_extension"]["from"] == 8
