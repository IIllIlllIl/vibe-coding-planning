from __future__ import annotations

import os
import subprocess
from pathlib import Path


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


def test_hpc_submit_batch_dry_run_uses_remote_env_file_without_local_key(
    tmp_path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\nexit 0\n",
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
    lines = result.stdout.splitlines()
    assert lines[lines.index("--cpus") + 1] == "2"
    assert lines[lines.index("--mem") + 1] == "8G"
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
