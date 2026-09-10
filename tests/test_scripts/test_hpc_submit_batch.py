from __future__ import annotations

import os
import subprocess
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_submit_batch.sh"


def test_hpc_submit_batch_help_succeeds() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--remote-env-file" in result.stdout
    assert "--remote-dataset-dir" in result.stdout
    assert "--remote-run-dir" in result.stdout
    assert "--remote-apptainer-cache-dir" in result.stdout
    assert "--remote-apptainer-tmp-dir" in result.stdout
    assert "--gepa-config" in result.stdout


def test_hpc_submit_batch_finds_ulhpc_submit_next_to_conda_exe(tmp_path) -> None:
    conda_bin = tmp_path / "conda-base" / "bin"
    conda_bin.mkdir(parents=True)
    fake_ulhpc = conda_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin"
    env["CONDA_EXE"] = str(conda_bin / "conda")
    env.pop("ULHPC_SUBMIT_BIN", None)

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--gepa-rules",
            "--gepa-config",
            "configs/gepa_verified_rules.yaml",
            "--remote-dir",
            "~/hpc_runs/conda-fallback-test",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "[hpc-submit] invoking ulhpc-submit" in result.stdout
    assert "--dry-run" in result.stdout


def test_hpc_submit_batch_dry_run_uses_remote_env_file_without_local_key(
    tmp_path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        """#!/usr/bin/env bash
args=("$@")
printf '%s\n' "$@"
for ((i=0; i<${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--config" ]]; then
    cat "${args[$((i + 1))]}"
  fi
done
exit 0
""",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_submit_batch"
    snapshot = local_root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    rules = local_root / "rules.md"
    rules.write_text("1. rule\n", encoding="utf-8")
    run_dir = local_root / "run"
    config = local_root / "gepa.yaml"
    config.write_text(
        f"""
paths:
  dataset_snapshot: {snapshot}
  initial_rules: {rules}
  run_dir: {run_dir}
checker:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
search:
  max_metric_calls: 1
docker: {{}}
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

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["DEEPSEEK_API_KEY"] = "secret-should-not-appear"
    ulhpc_config = tmp_path / "ulhpc.yaml"
    ulhpc_config.write_text(
        "host: example.invalid\nuser: tester\nsync_excludes:\n- .git\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--remote-dir",
            "~/hpc_runs/test",
            "--remote-dataset-dir",
            "~/hpc_datasets/test",
            "--remote-run-dir",
            "~/hpc_run_state/test",
            "--remote-apptainer-cache-dir",
            "/scratch/test/apptainer-cache",
            "--remote-apptainer-tmp-dir",
            "/scratch/test/apptainer-tmp",
            "--remote-env-file",
            "~/.config/vibe-coding-planning/deepseek.env",
            "--ulhpc-config",
            str(ulhpc_config),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "secret-should-not-appear" not in result.stdout
    assert "DEEPSEEK_API_KEY=\"$DEEPSEEK_API_KEY\"" not in result.stdout
    assert "--conda-env mini-swe" not in result.stdout
    assert "--submit-only" in result.stdout
    assert "--json" in result.stdout
    assert "--no-sync" in result.stdout
    assert "dry-run fixed-worktree sync:" in result.stdout
    lines = result.stdout.splitlines()
    assert lines[lines.index("--cpus") + 1] == "1"
    assert lines[lines.index("--mem") + 1] == "4G"
    assert "--module" in result.stdout
    assert "lang/Python/3.11" in result.stdout
    assert "tools/Apptainer" in result.stdout
    assert "--python" in result.stdout
    assert "python3" in result.stdout
    assert "--no-conda" in result.stdout
    assert "source \"$REMOTE_ENV_FILE\"" in result.stdout
    assert "~/.config/vibe-coding-planning/deepseek.env" in result.stdout
    assert "remote-dataset-snapshot=~/hpc_datasets/test/" in result.stdout
    assert "remote-run-snapshot=~/hpc_run_state/test/" in result.stdout
    assert "--stage-data" in result.stdout
    assert "--link-as" in result.stdout
    assert f"- {snapshot.name}" in result.stdout
    assert "--persistent-output" in result.stdout
    assert "~/hpc_run_state/test/" in result.stdout
    assert "remote-apptainer-cache-dir=/scratch/test/apptainer-cache" in (
        result.stdout
    )
    assert "remote-apptainer-tmp-dir=/scratch/test/apptainer-tmp" in (
        result.stdout
    )
    assert "--apptainer-cache-dir" in result.stdout
    assert "/scratch/test/apptainer-cache" in result.stdout
    assert "--apptainer-tmp-dir" in result.stdout
    assert "/scratch/test/apptainer-tmp" in result.stdout
    assert "--apptainer-sif-cache-dir" in result.stdout
    assert "/scratch/test/sif-cache" in result.stdout
    assert 'export APPTAINER_CACHEDIR="/scratch/test/apptainer-cache"' in (
        result.stdout
    )
    assert 'export APPTAINER_TMPDIR="/scratch/test/apptainer-tmp"' in (
        result.stdout
    )
    assert 'export ULHPC_APPTAINER_SIF_CACHE_DIR="/scratch/test/sif-cache"' in (
        result.stdout
    )
    assert (
        'mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR" '
        '"$ULHPC_APPTAINER_SIF_CACHE_DIR"'
    ) in result.stdout
    assert "--remote-ignore-extra" in result.stdout


def test_hpc_submit_batch_fixed_sync_excludes_persistent_and_staged_data(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    command_log = tmp_path / "commands.log"
    for name in ("ssh", "rsync", "ulhpc-submit"):
        executable = fake_bin / name
        executable.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '{name} %s\\n' \"$*\" >> {command_log}\n"
            + ("printf '{\"job_id\":\"123\"}\\n'\n" if name == "ulhpc-submit" else "")
            + "exit 0\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    family = REPO_ROOT / ".tmp_hpc_smoke" / "frozen_family"
    snapshot = family / "selected"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    rules = REPO_ROOT / ".tmp_hpc_smoke" / "fixed-rules.md"
    rules.write_text("1. rule\n", encoding="utf-8")
    config = REPO_ROOT / ".tmp_hpc_smoke" / "fixed-gepa.yaml"
    config.write_text(
        f"paths:\n  dataset_snapshot: {snapshot}\n  initial_rules: {rules}\n"
        f"  run_dir: {REPO_ROOT / '.tmp_hpc_smoke' / 'fixed-run'}\n"
        "task:\n  semantics: offline_reject_playbook_v1\n"
        "container:\n  runtime: none\n",
        encoding="utf-8",
    )
    submit_config = tmp_path / "ulhpc.yaml"
    submit_config.write_text(
        "host: example.invalid\nport: 8022\nuser: tester\n"
        "sync_excludes:\n- .git\n- output\n- .ulhpc_submit\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["ULHPC_SUBMIT_BIN"] = str(fake_bin / "ulhpc-submit")

    result = subprocess.run(
        [
            "bash", str(SCRIPT), "--gepa-rules", "--gepa-config", str(config),
            "--remote-dir", "/scratch/test/fixed-controller", "--ulhpc-config",
            str(submit_config), "--submit",
        ],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env,
    )

    assert result.returncode == 0, result.stderr
    logged = command_log.read_text(encoding="utf-8")
    assert "rsync -az --delete" in logged
    assert "--exclude output" in logged
    assert "--exclude frozen_family" in logged
    assert "/scratch/test/fixed-controller/" in logged
    assert "ulhpc-submit" in logged and "--no-sync" in logged
    assert "--persistent-output" in logged


def test_hpc_submit_batch_defaults_to_remote_user_from_config(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\nexit 0\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_hpc_submit_remote_user"
    snapshot = local_root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    rules = local_root / "rules.md"
    rules.write_text("1. rule\n", encoding="utf-8")
    run_dir = local_root / "run"
    config = local_root / "gepa.yaml"
    config.write_text(
        f"""
paths:
  dataset_snapshot: {snapshot}
  initial_rules: {rules}
  run_dir: {run_dir}
checker:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
search:
  max_metric_calls: 1
docker: {{}}
container:
  runtime: apptainer
  sif_cache_dir: /scratch/users/${{USER}}/vibe-coding-planning/shared/sif-cache
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )
    ulhpc_config = tmp_path / "ulhpc.yaml"
    ulhpc_config.write_text(
        """
user: remoteuser
python_module: lang/Python/3.11
container_module: tools/Apptainer
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["USER"] = "localuser"
    env.pop("ULHPC_USER", None)
    env.pop("VIBE_HPC_ROOT", None)
    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--gepa-rules",
            "--gepa-config",
            str(config),
            "--ulhpc-config",
            str(ulhpc_config),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "/scratch/users/remoteuser/vibe-coding-planning" in result.stdout
    assert "/scratch/users/localuser" not in result.stdout
