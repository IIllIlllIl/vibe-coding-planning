"""Unit tests for the Apptainer/SIF backend."""

from __future__ import annotations

import base64
import io
import re
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from src.environment.apptainer_env import (
    ApptainerEnvironment,
    ApptainerSifCache,
    _image_to_sif_name,
)
from src.exceptions import FatalError


def test_image_to_sif_name_mapping():
    assert (
        _image_to_sif_name(
            "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
        )
        == "swebench_sweb.eval.x86_64.astropy_1776_astropy-12907_latest.sif"
    )
    assert (
        _image_to_sif_name("python:3.12-slim")
        == "python_3.12-slim.sif"
    )
    assert _image_to_sif_name("repo.io/image:tag") == "repo.io_image_tag.sif"


class _FakeCapacityWindow:
    def __init__(self):
        self.acquisitions = 0
        self.min_free_gb = 0

    @staticmethod
    def lease():
        return nullcontext()

    @contextmanager  # type: ignore[misc]
    def image_acquisition(self):
        self.acquisitions += 1
        yield


class _TrackingLease:
    def __init__(self):
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *args):
        self.exited += 1
        return False


class _TrackingCapacityWindow:
    def __init__(self):
        self.lease_obj = _TrackingLease()

    def lease(self):
        return self.lease_obj


def test_sif_cache_returns_existing_file(tmp_path):
    cache_dir = tmp_path / "sifs"
    cache_dir.mkdir()
    sif = cache_dir / "python_3.12-slim.sif"
    sif.write_text("sif", encoding="utf-8")

    window = _FakeCapacityWindow()
    cache = ApptainerSifCache(cache_dir, window)
    assert cache.ensure("python:3.12-slim") == sif
    assert window.acquisitions == 0


def test_sif_cache_pulls_missing_image(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    window = _FakeCapacityWindow()
    cache = ApptainerSifCache(cache_dir, window)

    def fake_run(args, **kwargs):
        assert args[0] == "apptainer"
        assert args[1] == "pull"
        assert args[2] == "--force"
        assert args[4] == "docker://python:3.12-slim"
        assert Path(args[3]).name.startswith("python_3.12-slim.sif.tmp.")
        # Create the temporary SIF so ensure() can rename it atomically.
        Path(args[3]).write_text("sif", encoding="utf-8")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    sif = cache.ensure("python:3.12-slim")
    assert sif.name == "python_3.12-slim.sif"
    assert sif.exists()
    assert not list(cache_dir.glob("*.tmp.*"))
    assert window.acquisitions == 1


def test_sif_cache_raises_on_pull_failure(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    window = _FakeCapacityWindow()
    cache = ApptainerSifCache(cache_dir, window)

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            returncode=1,
            stdout="",
            stderr="pull failed",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FatalError, match="Apptainer pull failed"):
        cache.ensure("python:3.12-slim")
    assert not (cache_dir / "python_3.12-slim.sif").exists()
    assert not list(cache_dir.glob("*.tmp.*"))


def test_sif_cache_raises_when_apptainer_missing(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    window = _FakeCapacityWindow()
    cache = ApptainerSifCache(cache_dir, window)

    def fake_run(args, **kwargs):
        raise FileNotFoundError("apptainer")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(FatalError, match="Apptainer CLI not found"):
        cache.ensure("python:3.12-slim")


def test_environment_pulls_missing_sif_on_demand(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    cache_dir.mkdir()
    window = _FakeCapacityWindow()

    def fake_run(args, **kwargs):
        assert args[0] == "apptainer"
        if args[1] == "pull":
            Path(args[3]).write_text("sif", encoding="utf-8")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = ApptainerEnvironment(
        image="python:3.12-slim",
        cwd="/testbed",
        sif_cache_dir=cache_dir,
        capacity_window=window,
    )
    assert env._sif_path.exists()


def _make_env(cache_dir: Path, *, network_disabled: bool = False, run_args=None):
    cache_dir.mkdir(parents=True, exist_ok=True)
    sif = cache_dir / "python_3.12-slim.sif"
    sif.write_text("sif", encoding="utf-8")
    return ApptainerEnvironment(
        image="python:3.12-slim",
        cwd="/testbed",
        sif_cache_dir=cache_dir,
        capacity_window=_TrackingCapacityWindow(),
        network_disabled=network_disabled,
        run_args=run_args,
    )


def test_environment_execute_builds_expected_apptainer_args(
    tmp_path, monkeypatch
):
    cache_dir = tmp_path / "sifs"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = _make_env(cache_dir)
    calls.clear()

    result = env.execute("echo hello")

    assert result["returncode"] == 0
    args = calls[-1]
    assert args[0] == "apptainer"
    assert args[1] == "exec"
    assert args[2:4] == ["--cleanenv", "--no-home"]
    assert "--writable-tmpfs" in args
    home_env_index = args.index("HOME=/tmp/vibe_home")
    assert args[home_env_index - 1] == "--env"
    binds = [
        args[index + 1]
        for index, value in enumerate(args[:-1])
        if value == "--bind"
    ]
    assert any(value.endswith(":/tmp/vibe_home") for value in binds)
    env_index = args.index(f"GIT_CONFIG_GLOBAL={env._git_config_path}")
    assert args[env_index - 1] == "--env"
    assert str(cache_dir / "python_3.12-slim.sif") in args
    bash_cmd = args[-1]
    assert bash_cmd == "cd /testbed && echo hello"


def test_environment_creates_git_safe_config_at_startup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _make_env(cache_dir)

    startup_args, startup_kwargs = calls[0]
    startup_cmd = startup_args[-1]
    match = re.search(r"echo ([^|]+) \| base64 -d", startup_cmd)
    assert match is not None
    encoded = match.group(1).strip().strip("'\"")
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert "[safe]" in decoded
    assert "directory = /testbed" in decoded
    assert startup_kwargs["timeout"] is None


def test_environment_applies_network_and_run_args(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = _make_env(
        cache_dir,
        network_disabled=True,
        run_args=["--bind", "/host:/container:ro"],
    )
    calls.clear()
    env.execute("cmd")

    args = calls[-1]
    assert "--net" in args
    assert "--network" in args
    assert "none" in args
    bind_index = args.index("--bind")
    assert args[bind_index + 1] == "/host:/container:ro"


def test_environment_host_workdir_is_initialized_and_bound(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"
    host_workdir = tmp_path / "phase-workdir"
    calls = []
    popen_calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            popen_calls.append(args)
            self.stdout = io.BytesIO(b"fake-tar")
            self.stderr = io.BytesIO(b"")

        def wait(self):
            return 0

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "tar":
            assert args[:3] == ["tar", "-xf", "-"]
            assert args[3] == "-C"
            assert args[4] == str(host_workdir)
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(subprocess, "run", fake_run)

    env = _make_env(cache_dir, run_args=None)
    env.cleanup()
    calls.clear()
    popen_calls.clear()

    env = ApptainerEnvironment(
        image="python:3.12-slim",
        cwd="/testbed",
        sif_cache_dir=cache_dir,
        capacity_window=_TrackingCapacityWindow(),
        host_workdir=host_workdir,
    )
    assert popen_calls[0][2:4] == ["--cleanenv", "--no-home"]
    assert "HOME=/tmp/vibe_home" in popen_calls[0]
    assert popen_calls[0][-1] == "cd /testbed && tar -cf - ."

    calls.clear()
    env.execute("git status")

    args = calls[-1]
    binds = [
        args[index + 1]
        for index, value in enumerate(args[:-1])
        if value == "--bind"
    ]
    assert f"{host_workdir}:/testbed" in binds


def test_environment_uses_phase_local_home_and_removes_it_on_cleanup(
    tmp_path, monkeypatch
):
    cache_dir = tmp_path / "sifs"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    env = _make_env(cache_dir)
    isolated_home = Path(env._isolated_home.name)
    assert isolated_home.is_dir()

    env.cleanup()

    assert not isolated_home.exists()


def test_environment_get_template_vars_and_cleanup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "sifs"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    window = _TrackingCapacityWindow()
    cache_dir.mkdir(parents=True, exist_ok=True)
    sif = cache_dir / "python_3.12-slim.sif"
    sif.write_text("sif", encoding="utf-8")
    env = ApptainerEnvironment(
        image="python:3.12-slim",
        cwd="/evidence",
        sif_cache_dir=cache_dir,
        capacity_window=window,
    )

    assert env.get_template_vars() == {"cwd": "/evidence"}
    assert window.lease_obj.entered == 1
    env.cleanup()
    assert window.lease_obj.exited == 1
    # Cleanup is idempotent.
    env.cleanup()
    assert window.lease_obj.exited == 1
