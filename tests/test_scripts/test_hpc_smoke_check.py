from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "hpc_smoke_check.sh"


def test_hpc_smoke_help_succeeds() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--submit" in result.stdout
    assert "--sync-dry-run" in result.stdout
    assert "SSH connectivity" in result.stdout


def test_hpc_smoke_dry_run_checks_connectivity_without_sync(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "args.txt"
    fake = fake_bin / "ulhpc-submit"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$ULHPC_CAPTURE_ARGS\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["ULHPC_CAPTURE_ARGS"] = str(capture)
    env["ULHPC_LOG_DIR"] = str(tmp_path / "logs")

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--user",
            "dummy",
            "--remote-dir",
            "~/hpc_runs/vibe-smoke",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    args = capture.read_text(encoding="utf-8").splitlines()
    assert "--dry-run" in args
    assert "--no-sync" in args
    assert "--user" in args
    assert "dummy" in args
    assert "python -m src.environment.docker_env maintain" in "\n".join(args)


def test_hpc_smoke_submit_omits_dry_run_and_no_sync(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "args.txt"
    fake = fake_bin / "ulhpc-submit"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$ULHPC_CAPTURE_ARGS\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["ULHPC_CAPTURE_ARGS"] = str(capture)
    env["ULHPC_LOG_DIR"] = str(tmp_path / "logs")

    result = subprocess.run(
        [
            "bash",
            str(SCRIPT),
            "--submit",
            "--user",
            "dummy",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    args = capture.read_text(encoding="utf-8").splitlines()
    assert "--dry-run" not in args
    assert "--no-sync" not in args


def test_hpc_smoke_uses_default_private_config_when_present(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "args.txt"
    fake = fake_bin / "ulhpc-submit"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$ULHPC_CAPTURE_ARGS\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    config_path = REPO_ROOT / "configs" / "ulhpc_submit.yaml"
    config_existed = config_path.exists()
    original = config_path.read_text(encoding="utf-8") if config_existed else None
    config_path.write_text("user: dummy\n", encoding="utf-8")
    try:
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["ULHPC_CAPTURE_ARGS"] = str(capture)
        env["ULHPC_LOG_DIR"] = str(tmp_path / "logs")

        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        if config_existed and original is not None:
            config_path.write_text(original, encoding="utf-8")
        else:
            config_path.unlink(missing_ok=True)

    assert result.returncode == 0
    args = capture.read_text(encoding="utf-8").splitlines()
    assert "--config" in args
    assert str(config_path) in args
