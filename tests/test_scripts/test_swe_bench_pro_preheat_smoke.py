from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_submit_swe_bench_pro_preheat_smoke.sh"


def test_pro_preheat_smoke_uses_node_local_tmp_and_one_frozen_image(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_submit = fake_bin / "ulhpc-submit"
    fake_submit.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_submit.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert "--no-sync" in result.stdout
    assert "python3 -m pip install" not in result.stdout
    assert "--cpus\n1" in result.stdout
    assert "--mem\n4G" in result.stdout
    assert "--time\n02:00:00" in result.stdout
    assert "/tmp/vibe-pro-preheat-${SLURM_JOB_ID}" in result.stdout
    assert ".single-writer-preheat.lock" in result.stdout
    assert "apptainer pull" in result.stdout
    assert "5069b09e5f64428dce59b33455c8bb17fe577070" in result.stdout
    assert "summary cached=$cached pulled=$pulled failed=$failures requested=1" in result.stdout
    assert "--submit\n" not in result.stdout


def test_pro_preheat_smoke_rejects_non_frozen_instance() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--instance-id", "not-a-frozen-case"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "exactly one frozen quick25 request" in result.stderr


def test_pro_preheat_recovery_visits_all_frozen_requests_with_measured_memory(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_submit = fake_bin / "ulhpc-submit"
    fake_submit.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_submit.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT), "--all-missing", "--mem", "6G", "--time", "03:00:00"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "selection=all-missing" in result.stdout
    assert "request_count=25" in result.stdout
    assert "--mem\n6G" in result.stdout
    assert "--time\n03:00:00" in result.stdout
    assert result.stdout.count("pull_one jefzda/sweap-images:") == 25
    assert "pro-q25-node-tmp-recovery" in result.stdout
