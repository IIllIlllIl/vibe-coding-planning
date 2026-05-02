"""Tests for src/environment/docker_env.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.config import DockerConfig
from src.environment.docker_env import DockerEnvWrapper
from src.exceptions import FatalError


class MockDockerEnvironment:
    """Mock class that mimics mini-swe-agent DockerEnvironment."""

    def __init__(self, image: str, workdir: str, run_args: list[str]) -> None:
        self.image = image
        self.workdir = workdir
        self.run_args = run_args
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def execute(self, command: str) -> str:
        return f"executed: {command}"


@pytest.fixture
def docker_config() -> DockerConfig:
    return DockerConfig(
        image_builder_script="./scripts/build.sh",
        workdir="/testbed",
        codebase_mount_options="ro",
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
        assert wrapper._env.workdir == "/testbed"
        assert wrapper._env._started is True

    @patch("src.environment.docker_env._import_docker_env")
    def test_ro_mount_in_run_args(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(
            image="swebench/astropy:latest",
            workdir="/testbed",
            ro_mount_source="/host/code",
        )

        run_args = wrapper._env.run_args
        assert any("readonly" in arg for arg in run_args)
        assert any("/host/code" in arg for arg in run_args)
        assert any("/testbed" in arg for arg in run_args)


class TestExecute:
    @patch("src.environment.docker_env._import_docker_env")
    def test_execute_returns_result(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        result = wrapper.execute("ls /testbed")
        assert result == "executed: ls /testbed"

    def test_execute_without_start_raises(self, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        with pytest.raises(FatalError, match="not started"):
            wrapper.execute("ls")


class TestStop:
    @patch("src.environment.docker_env._import_docker_env")
    def test_stop_sets_started_false(self, mock_import, docker_config):
        mock_import.return_value = MockDockerEnvironment
        wrapper = DockerEnvWrapper(docker_config)
        wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
        env_ref = wrapper._env
        wrapper.stop()
        assert env_ref._started is False

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


class TestMissingDependency:
    @patch(
        "src.environment.docker_env._import_docker_env",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, docker_config):
        wrapper = DockerEnvWrapper(docker_config)
        with pytest.raises(FatalError, match="mini-swe-agent"):
            wrapper.start(image="swebench/astropy:latest", workdir="/testbed")
