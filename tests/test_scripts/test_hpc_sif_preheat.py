from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.tools import hpc_sif_preheat_loop


REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMIT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "submit_apptainer_sif_preheat.sh"


def _write_preheat_config(root: Path) -> Path:
    snapshot = root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    config = root / "gepa.yaml"
    config.write_text(
        f"""
paths:
  dataset_snapshot: {snapshot}
container:
  runtime: apptainer
  sif_cache_dir: /scratch/test/sif-cache
""",
        encoding="utf-8",
    )
    return config


def test_submit_apptainer_sif_preheat_delegates_to_ulhpc_submit(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_sif_preheat_submit"
    config = _write_preheat_config(local_root)
    ulhpc_config = tmp_path / "ulhpc.yaml"
    ulhpc_config.write_text(
        """
python_module: lang/Python/3.11.5-GCCcore-13.2.0
container_module: tools/Apptainer
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SUBMIT_SCRIPT),
            "--config",
            str(config.relative_to(REPO_ROOT)),
            "--ulhpc-config",
            str(ulhpc_config),
            "--remote-dir",
            "~/hpc_runs/preheat",
            "--remote-dataset-dir",
            "~/hpc_datasets/preheat",
            "--remote-apptainer-cache-dir",
            "/scratch/test/apptainer-cache",
            "--remote-apptainer-tmp-dir",
            "/scratch/test/apptainer-tmp",
            "--time",
            "00:30:00",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "--submit-only" in result.stdout
    assert "--json" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--module" in result.stdout
    assert "lang/Python/3.11.5-GCCcore-13.2.0" in result.stdout
    assert "tools/Apptainer" in result.stdout
    assert "--python" in result.stdout
    assert "python3" in result.stdout
    assert "--no-conda" in result.stdout
    assert "--stage-data" in result.stdout
    assert "--link-as" in result.stdout
    assert "--apptainer-cache-dir" in result.stdout
    assert "/scratch/test/apptainer-cache" in result.stdout
    assert "--apptainer-tmp-dir" in result.stdout
    assert "/scratch/test/apptainer-tmp" in result.stdout
    assert "--apptainer-sif-cache-dir" in result.stdout
    assert "/scratch/test/sif-cache" in result.stdout
    assert "scripts/tools/prepare_apptainer_sifs.py" in result.stdout
    assert "skipping dependency install" in result.stdout
    assert "python3 -m pip install --quiet --user -r requirements.txt" not in result.stdout
    assert "sbatch" not in result.stdout
    assert "module load" not in result.stdout


def test_submit_apptainer_sif_preheat_can_install_deps_when_requested(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_sif_preheat_install_deps"
    config = _write_preheat_config(local_root)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SUBMIT_SCRIPT),
            "--config",
            str(config.relative_to(REPO_ROOT)),
            "--remote-dir",
            "~/hpc_runs/preheat",
            "--remote-dataset-dir",
            "~/hpc_datasets/preheat",
            "--install-deps",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "python3 -m pip install --quiet --user -r requirements.txt" in result.stdout


def test_hpc_sif_preheat_loop_submit_slice_uses_local_wrapper(monkeypatch, tmp_path: Path) -> None:
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_sif_preheat_loop"
    config_path = _write_preheat_config(local_root)
    preheat_script = tmp_path / "submit_apptainer_sif_preheat.sh"
    ulhpc_config = tmp_path / "ulhpc.yaml"

    captured: list[list[str]] = []

    def fake_run_command(command: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, '{"job_id":"12345"}\n', "")

    monkeypatch.setattr(hpc_sif_preheat_loop, "run_command", fake_run_command)
    config = hpc_sif_preheat_loop.Config(
        gepa_config=config_path,
        preheat_script=preheat_script,
        ulhpc_config=ulhpc_config,
        remote_project_dir="/scratch/test/preheat-work",
        remote_dataset_dir="/scratch/test/datasets",
        sif_cache_dir="/scratch/test/sif-cache",
        job_name="preheat-test",
        slice_time="00:30:00",
        check_interval_seconds=0,
        poll_interval_seconds=0,
        max_runs=1,
        cpus="1",
        mem="4G",
        timeout="120",
        max_attempts="2",
        retry_backoff="0",
        ssh_target="tester@example.invalid",
        ssh_port="22",
        ssh_key="",
        submit=True,
    )

    assert hpc_sif_preheat_loop.submit_slice(config) == "12345"
    assert len(captured) == 1
    command = captured[0]
    assert command[:2] == ["bash", str(preheat_script)]
    assert "--remote-dir" in command
    assert "/scratch/test/preheat-work" in command
    assert "--remote-dataset-dir" in command
    assert "/scratch/test/datasets" in command
    assert "--ulhpc-config" in command
    assert str(ulhpc_config) in command
    assert "--submit" in command
