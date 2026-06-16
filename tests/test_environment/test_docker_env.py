"""Tests for src/environment/docker_env.py (mini-swe-agent 1.17.5)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch
from types import SimpleNamespace
import subprocess
import threading
import time

import pytest

import src.environment.docker_env as docker_env_module
from src.config import DockerConfig
from src.environment.docker_env import (
    DockerCapacityWindow,
    DockerEnvWrapper,
    _resolve_polybench_image,
    cleanup_docker_image_cache,
    is_docker_storage_error,
    prune_dangling_images,
    remove_docker_image,
    reset_project_docker_resources,
)
from src.exceptions import FatalError


class MockDockerEnvironment:
    """Mock class that mimics mini-swe-agent 1.17.5 DockerEnvironment."""

    def __init__(
        self,
        *,
        image: str,
        cwd: str,
        run_args: Optional[list[str]] = None,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> None:
        self.image = image
        self.cwd = cwd
        self.run_args = run_args or []
        self.timeout = timeout
        self.kwargs = kwargs
        self._cleaned_up = False

    def execute(self, command: str) -> dict:
        return {"output": f"executed: {command}", "returncode": 0}

    def get_template_vars(self) -> dict[str, str]:
        return {"cwd": self.cwd}

    def cleanup(self) -> None:
        self._cleaned_up = True


@pytest.fixture
def docker_config() -> DockerConfig:
    return DockerConfig(
        image_builder_script="./scripts/build.sh",
        workdir="/testbed",
        timeout=30,
    )


@pytest.fixture(autouse=True)
def isolated_default_docker_window(tmp_path, monkeypatch):
    """Keep wrapper tests away from the real shared Docker window."""
    window = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=75,
        min_free_gb=20,
        lock_dir=tmp_path / "docker-window",
    )
    monkeypatch.setattr(window, "ensure_capacity", lambda: None)
    monkeypatch.setattr(window, "maintain", lambda: None)
    monkeypatch.setattr(docker_env_module, "_DEFAULT_WINDOW", window)


class TestStart:
    @patch("src.environment.docker_env._import_docker_env")
    def test_starts_container_with_correct_params(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")

        assert wrapper._env is not None
        assert wrapper._env.image == "swebench/astropy:latest"
        assert wrapper._env.cwd == "/testbed"

    @patch("src.environment.docker_env._import_docker_env")
    def test_mount_in_run_args(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(
            image="swebench/astropy:latest",
            workdir="/testbed",
            mount_source="/host/code",
        )

        run_args = wrapper._env.run_args
        assert not any("readonly" in arg for arg in run_args)
        assert any("/host/code" in arg for arg in run_args)
        assert any("/testbed" in arg for arg in run_args)
        assert "--rm" in run_args

    @patch("src.environment.docker_env._import_docker_env")
    def test_timeout_passed_to_env(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed", timeout=120)
        assert wrapper._env is not None

    @patch("src.environment.docker_env._import_docker_env")
    def test_concurrent_verified_starts_pull_image_once(
        self, mock_import, tmp_path, docker_config, monkeypatch
    ):
        mock_import.return_value = MockDockerEnvironment
        window = DockerCapacityWindow(
            max_concurrent=2,
            max_cached_images=2,
            min_free_gb=20,
            lock_dir=tmp_path / "verified-pull",
        )
        monkeypatch.setattr(window, "ensure_capacity", lambda: None)
        monkeypatch.setattr(window, "maintain", lambda: None)
        image = "swebench/sweb.eval.x86_64.django_1776_django-14765:latest"
        local_images: set[str] = set()
        state_lock = threading.Lock()
        pull_count = 0

        def fake_run(args, **kwargs):
            nonlocal pull_count
            target = args[-1]
            if args[:3] == ["docker", "image", "inspect"]:
                with state_lock:
                    exists = target in local_images
                return SimpleNamespace(
                    returncode=0 if exists else 1,
                    stdout="",
                    stderr="",
                )
            if args[:2] == ["docker", "pull"]:
                with state_lock:
                    pull_count += 1
                time.sleep(0.03)
                with state_lock:
                    local_images.add(target)
                return SimpleNamespace(returncode=0, stdout="pulled", stderr="")
            raise AssertionError(args)

        wrappers = [DockerEnvWrapper(docker_config, window) for _ in range(2)]

        def start(wrapper):
            wrapper.start(image=image, workdir="/testbed")

        threads = [threading.Thread(target=start, args=(wrapper,)) for wrapper in wrappers]
        with patch(
            "src.environment.docker_env.subprocess.run",
            side_effect=fake_run,
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert pull_count == 1
        assert all(wrapper._env is not None for wrapper in wrappers)
        for wrapper in wrappers:
            wrapper.stop()


class TestExecute:
    @patch("src.environment.docker_env._import_docker_env")
    def test_execute_returns_result(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        result = wrapper.execute("ls /testbed")
        assert result == {"output": "executed: ls /testbed", "returncode": 0}

    def test_execute_without_start_raises(self, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        with pytest.raises(FatalError, match="not started"):
            wrapper.execute("ls")

    @patch("src.environment.docker_env._import_docker_env")
    def test_execute_non_dict_result(self, mock_import, docker_config):
        class NonDictEnv(MockDockerEnvironment):
            def execute(self, command):
                return f"raw output: {command}"

        mock_import.return_value = NonDictEnv
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        result = wrapper.execute("echo hi")
        assert result == {"output": "raw output: echo hi", "returncode": 0}


class TestStop:
    @patch("src.environment.docker_env._import_docker_env")
    def test_stop_calls_cleanup(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        env_ref = wrapper._env
        wrapper.stop()
        assert env_ref._cleaned_up is True
        assert wrapper._env is None

    def test_stop_without_start_is_noop(self, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.stop()  # should not raise


class TestGetTemplateVars:
    def test_returns_empty_when_not_started(self, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        assert wrapper.get_template_vars() == {}

    @patch("src.environment.docker_env._import_docker_env")
    def test_returns_env_vars_when_started(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        assert wrapper.get_template_vars() == {"cwd": "/testbed"}


class TestContextManager:
    @patch("src.environment.docker_env._import_docker_env")
    def test_context_manager_stops_on_exit(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")

        with wrapper:
            pass

        assert wrapper._env is None


class TestMissingDependency:
    @patch(
        "src.environment.docker_env._import_docker_env",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        with pytest.raises(FatalError, match="mini-swe-agent"):
            wrapper.start(image="swebench/astropy:latest", workdir="/testbed")


class TestPolybenchImageFallback:
    def test_detects_docker_storage_error(self):
        msg = "write /var/lib/desktop-containerd/daemon/io.containerd.metadata.v1.bolt/meta.db: input/output error"
        assert is_docker_storage_error(msg) is True
        assert is_docker_storage_error("manifest unknown") is False

    def test_resolve_polybench_image_uses_v10_fallback(self):
        image = "ghcr.io/timesler/swe-polybench.eval.x86_64.test__repo-1:v1.1"

        def fake_run(args, **kwargs):
            cmd = args[:3]
            target = args[-1]
            if cmd == ["docker", "image", "inspect"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:2] == ["docker", "pull"]:
                if target.endswith(":v1.0"):
                    return SimpleNamespace(returncode=0, stdout="pulled", stderr="")
                raise subprocess.CalledProcessError(
                    1, args, output="", stderr="manifest unknown"
                )
            raise AssertionError(args)

        with patch("src.environment.docker_env.subprocess.run", side_effect=fake_run):
            resolved = _resolve_polybench_image(image, timeout=60)

        assert resolved.endswith(":v1.0")

    def test_resolve_polybench_image_raises_when_all_tags_fail(self):
        image = "ghcr.io/timesler/swe-polybench.eval.x86_64.test__repo-1:v1.1"

        def fake_run(args, **kwargs):
            if args[:3] == ["docker", "image", "inspect"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:2] == ["docker", "pull"]:
                raise subprocess.CalledProcessError(
                    1, args, output="", stderr="denied"
                )
            raise AssertionError(args)

        with patch("src.environment.docker_env.subprocess.run", side_effect=fake_run):
            with pytest.raises(FatalError, match="Unable to obtain PolyBench Docker image"):
                _resolve_polybench_image(image, timeout=60)

    def test_resolve_polybench_image_builds_official_fallback(self):
        image = "ghcr.io/timesler/swe-polybench.eval.x86_64.test__repo-1:v1.1"
        instance_info = {"instance_id": "test__repo-1"}

        def fake_run(args, **kwargs):
            if args[:3] == ["docker", "image", "inspect"]:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if args[:2] == ["docker", "pull"]:
                raise subprocess.CalledProcessError(
                    1, args, output="", stderr="manifest unknown"
                )
            raise AssertionError(args)

        with (
            patch("src.environment.docker_env.subprocess.run", side_effect=fake_run),
            patch(
                "src.environment.polybench_image.build_polybench_image_from_official_dockerfile",
                return_value="polybench_python_test__repo-1",
            ) as mock_build,
        ):
            resolved = _resolve_polybench_image(
                image,
                timeout=60,
                instance_info=instance_info,
                build_fallback=True,
            )

        assert resolved == "polybench_python_test__repo-1"
        mock_build.assert_called_once_with(instance_info, build_timeout=3600)

    def test_concurrent_resolvers_pull_same_image_once(self, tmp_path):
        image = (
            "ghcr.io/timesler/"
            "swe-polybench.eval.x86_64.test__repo-1:v1.1"
        )
        window = DockerCapacityWindow(
            max_concurrent=2,
            max_cached_images=2,
            min_free_gb=20,
            lock_dir=tmp_path / "pull-lock",
        )
        local_images: set[str] = set()
        state_lock = threading.Lock()
        pull_count = 0

        def fake_run(args, **kwargs):
            nonlocal pull_count
            target = args[-1]
            if args[:3] == ["docker", "image", "inspect"]:
                with state_lock:
                    exists = target in local_images
                return SimpleNamespace(
                    returncode=0 if exists else 1,
                    stdout="",
                    stderr="",
                )
            if args[:2] == ["docker", "pull"]:
                with state_lock:
                    pull_count += 1
                time.sleep(0.03)
                with state_lock:
                    local_images.add(target)
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise AssertionError(args)

        results: list[str] = []

        def resolve():
            results.append(
                _resolve_polybench_image(
                    image,
                    timeout=60,
                    capacity_window=window,
                )
            )

        threads = [threading.Thread(target=resolve) for _ in range(2)]
        with patch(
            "src.environment.docker_env.subprocess.run",
            side_effect=fake_run,
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert results == [image, image]
        assert pull_count == 1


class TestDockerImageCacheCleanup:
    @patch("src.environment.docker_env.remove_docker_image", return_value=True)
    @patch("src.environment.docker_env._list_container_image_ids")
    @patch("src.environment.docker_env._list_project_docker_images")
    def test_retains_newest_idle_project_images_only(
        self, mock_images, mock_references, mock_remove
    ):
        mock_references.return_value = {"id-active"}
        mock_images.return_value = [
            {"ref": "project:old", "id": "id-old", "created": "2026-01-01"},
            {
                "ref": "project:active",
                "id": "id-active",
                "created": "2025-01-01",
            },
            {"ref": "project:new", "id": "id-new", "created": "2026-01-03"},
            {"ref": "project:middle", "id": "id-mid", "created": "2026-01-02"},
        ]

        count = cleanup_docker_image_cache(max_cached_images=2)

        assert count == 1
        mock_remove.assert_called_once_with("project:old")

    @patch("src.environment.docker_env.remove_docker_image")
    @patch("src.environment.docker_env._list_container_image_ids")
    @patch("src.environment.docker_env._list_project_docker_images")
    def test_referenced_images_do_not_consume_idle_cache_slots(
        self, mock_images, mock_references, mock_remove
    ):
        mock_references.return_value = {"active-1", "active-2"}
        mock_images.return_value = [
            {"ref": "project:a", "id": "active-1", "created": "2026-01-01"},
            {"ref": "project:b", "id": "active-2", "created": "2026-01-02"},
            {"ref": "project:c", "id": "idle-1", "created": "2026-01-03"},
            {"ref": "project:d", "id": "idle-2", "created": "2026-01-04"},
        ]

        count = cleanup_docker_image_cache(max_cached_images=2)

        assert count == 0
        mock_remove.assert_not_called()

    def test_image_removal_is_not_forced(self):
        with patch(
            "src.environment.docker_env.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0, stdout="deleted", stderr=""
            ),
        ) as mock_run:
            assert remove_docker_image("project:old") is True

        assert mock_run.call_args.args[0] == [
            "docker",
            "image",
            "rm",
            "project:old",
        ]

    @patch("src.environment.docker_env.remove_docker_image")
    @patch("src.environment.docker_env._list_container_image_ids")
    @patch("src.environment.docker_env._list_project_docker_images")
    def test_noop_when_within_limit(
        self, mock_images, mock_references, mock_remove
    ):
        mock_references.return_value = set()
        mock_images.return_value = [
            {"ref": "project:a", "id": "id1", "created": "2026-01-01"}
        ]

        count = cleanup_docker_image_cache(max_cached_images=2)

        assert count == 0
        mock_remove.assert_not_called()


class TestDockerCapacityWindow:
    def test_config_lookup_preserves_explicit_parallel_window(
        self, docker_config, monkeypatch, tmp_path
    ):
        window = DockerCapacityWindow(
            max_concurrent=3,
            max_cached_images=docker_config.max_cached_images,
            min_free_gb=docker_config.min_free_gb,
            lock_dir=tmp_path / "configured",
        )
        monkeypatch.setattr(docker_env_module, "_DEFAULT_WINDOW", window)

        resolved = docker_env_module.get_docker_capacity_window(docker_config)

        assert resolved is window
        assert resolved.max_concurrent == 3

    def test_image_acquisition_is_shared_between_windows(self, tmp_path):
        windows = [
            DockerCapacityWindow(
                max_concurrent=2,
                max_cached_images=2,
                min_free_gb=20,
                lock_dir=tmp_path / "shared-pull",
            )
            for _ in range(2)
        ]
        active = 0
        peak = 0
        state_lock = threading.Lock()

        def acquire(window):
            nonlocal active, peak
            with window.image_acquisition():
                with state_lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with state_lock:
                    active -= 1

        threads = [
            threading.Thread(target=acquire, args=(window,))
            for window in windows
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert peak == 1

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_shared_window_bounds_concurrency(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=200 * 1024**3)
        window = DockerCapacityWindow(
            max_concurrent=2,
            max_cached_images=4,
            min_free_gb=20,
            lock_dir=tmp_path / "shared",
        )
        barrier = threading.Barrier(2)

        def use_slot():
            with window.lease():
                barrier.wait(timeout=1)
                time.sleep(0.02)

        threads = [threading.Thread(target=use_slot) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert window.peak_active == 2
        assert window.active == 0
        assert mock_cleanup.call_count >= 1
        assert mock_dangling.call_count == 4
        mock_build.assert_not_called()

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_low_disk_blocks_lease_after_cleanup(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=5 * 1024**3)
        window = DockerCapacityWindow(
            max_concurrent=1,
            max_cached_images=2,
            min_free_gb=20,
            lock_dir=tmp_path / "low-disk",
        )

        with pytest.raises(FatalError, match="blocked container launch"):
            with window.lease():
                pass

        mock_cleanup.assert_not_called()
        mock_dangling.assert_called_once_with()
        mock_build.assert_called_once_with(aggressive=True)
        assert window.active == 0

    def test_rejects_cache_smaller_than_window(self):
        with pytest.raises(ValueError, match="at least max_concurrent"):
            DockerCapacityWindow(
                max_concurrent=3,
                max_cached_images=2,
                min_free_gb=20,
            )

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_nested_lease_is_reentrant(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=200 * 1024**3)
        window = DockerCapacityWindow(
            max_concurrent=1,
            max_cached_images=2,
            min_free_gb=20,
            lock_dir=tmp_path / "nested",
        )

        with window.lease():
            assert window.active == 1
            with window.lease():
                assert window.active == 1

        assert window.active == 0
        mock_cleanup.assert_called_once_with(2)
        mock_dangling.assert_called_once_with()
        mock_build.assert_not_called()

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_separate_windows_share_interprocess_slots(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=200 * 1024**3)
        windows = [
            DockerCapacityWindow(
                max_concurrent=1,
                max_cached_images=2,
                min_free_gb=20,
                lock_dir=tmp_path,
            )
            for _ in range(2)
        ]
        active = 0
        peak = 0
        state_lock = threading.Lock()

        def use_slot(window):
            nonlocal active, peak
            with window.lease():
                with state_lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with state_lock:
                    active -= 1

        threads = [
            threading.Thread(target=use_slot, args=(window,))
            for window in windows
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert peak == 1
        mock_build.assert_not_called()

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_maintenance_defers_tagged_eviction_while_slot_is_active(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=200 * 1024**3)
        window = DockerCapacityWindow(
            max_concurrent=2,
            max_cached_images=4,
            min_free_gb=20,
            lock_dir=tmp_path / "active-slot",
        )
        active_usage = window._acquire_usage_lock()
        try:
            window.maintain()
        finally:
            window._release_file_lock(active_usage)

        mock_cleanup.assert_not_called()
        mock_dangling.assert_called_once_with()
        mock_build.assert_not_called()

    @patch("src.environment.docker_env.prune_build_cache")
    @patch("src.environment.docker_env.prune_dangling_images")
    @patch("src.environment.docker_env.cleanup_docker_image_cache")
    @patch("src.environment.docker_env.shutil.disk_usage")
    def test_moderate_disk_pressure_uses_age_filtered_build_prune(
        self,
        mock_disk_usage,
        mock_cleanup,
        mock_dangling,
        mock_build,
        tmp_path,
    ):
        mock_disk_usage.return_value = SimpleNamespace(free=100 * 1024**3)
        window = DockerCapacityWindow(
            max_concurrent=1,
            max_cached_images=2,
            min_free_gb=20,
            lock_dir=tmp_path / "moderate-disk",
        )

        window.maintain()

        mock_cleanup.assert_called_once_with(2)
        mock_dangling.assert_called_once_with()
        mock_build.assert_called_once_with(aggressive=False)


@patch("src.environment.docker_env.subprocess.run")
def test_prune_dangling_images_uses_non_aggressive_image_prune(mock_run):
    mock_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

    prune_dangling_images()

    mock_run.assert_called_once_with(
        ["docker", "image", "prune", "-f"],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@patch("src.environment.docker_env.prune_docker_resources")
@patch("src.environment.docker_env.run_docker_cli")
def test_reset_project_docker_resources_uses_manager_commands(
    mock_run_cli, mock_prune
):
    mock_run_cli.side_effect = [
        SimpleNamespace(returncode=0, stdout="one\ntwo\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ]

    reset_project_docker_resources()

    assert mock_run_cli.call_args_list[0].args[0] == [
        "docker",
        "container",
        "ls",
        "-aq",
        "--filter",
        "name=minisweagent-",
    ]
    assert mock_run_cli.call_args_list[1].args[0] == [
        "docker",
        "container",
        "rm",
        "-f",
        "one",
        "two",
    ]
    assert mock_run_cli.call_args_list[2].args[0] == [
        "docker",
        "image",
        "prune",
        "-af",
    ]
    mock_prune.assert_called_once_with(max_cached_images=0)
