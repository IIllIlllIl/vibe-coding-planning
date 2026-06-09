"""Tests for src/environment/docker_env.py (mini-swe-agent 1.17.5)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch
from types import SimpleNamespace
import subprocess

import pytest

from src.config import DockerConfig
from src.environment.docker_env import (
    DockerEnvWrapper,
    _resolve_polybench_image,
    cleanup_docker_image_cache,
    is_docker_storage_error,
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


class TestContextManager:
    @patch("src.environment.docker_env._import_docker_env")
    def test_context_manager_stops_on_exit(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")

        with wrapper:
            pass

        assert wrapper._env is None


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


class TestDockerImageCacheCleanup:
    def test_retains_newest_project_images_only(self):
        removed: list[str] = []

        def fake_run(args, **kwargs):
            if args[:4] == ["docker", "image", "ls", "--format"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "swebench/sweb.eval.x86_64.repo_1776_a:latest\tid1\n"
                        "ghcr.io/timesler/swe-polybench.eval.x86_64.repo__b:v1.1\tid2\n"
                        "ubuntu:latest\tid3\n"
                        "jefzda/sweap-images:repo-c\tid4\n"
                    ),
                    stderr="",
                )
            if args[:3] == ["docker", "image", "inspect"]:
                ref = args[3]
                created = {
                    "swebench/sweb.eval.x86_64.repo_1776_a:latest": "2026-01-01T00:00:00Z",
                    "ghcr.io/timesler/swe-polybench.eval.x86_64.repo__b:v1.1": "2026-01-03T00:00:00Z",
                    "jefzda/sweap-images:repo-c": "2026-01-02T00:00:00Z",
                }[ref]
                return SimpleNamespace(returncode=0, stdout=created + "\n", stderr="")
            if args[:4] == ["docker", "image", "rm", "-f"]:
                removed.append(args[4])
                return SimpleNamespace(returncode=0, stdout="deleted", stderr="")
            raise AssertionError(args)

        with patch("src.environment.docker_env.subprocess.run", side_effect=fake_run):
            count = cleanup_docker_image_cache(max_cached_images=2)

        assert count == 1
        assert removed == ["swebench/sweb.eval.x86_64.repo_1776_a:latest"]

    def test_noop_when_within_limit(self):
        def fake_run(args, **kwargs):
            if args[:4] == ["docker", "image", "ls", "--format"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="swebench/sweb.eval.x86_64.repo_1776_a:latest\tid1\n",
                    stderr="",
                )
            if args[:3] == ["docker", "image", "inspect"]:
                return SimpleNamespace(returncode=0, stdout="2026-01-01T00:00:00Z\n", stderr="")
            if args[:4] == ["docker", "image", "rm", "-f"]:
                raise AssertionError("should not remove images within limit")
            raise AssertionError(args)

        with patch("src.environment.docker_env.subprocess.run", side_effect=fake_run):
            count = cleanup_docker_image_cache(max_cached_images=2)

        assert count == 0
