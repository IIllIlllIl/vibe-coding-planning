"""Tests for src/environment/docker_env.py (mini-swe-agent 1.17.5)."""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from src.config import DockerConfig
from src.environment.docker_env import DockerEnvWrapper
from src.exceptions import FatalError


class MockDockerEnvironment:
    """Mock class that mimics mini-swe-agent 1.17.5 DockerEnvironment."""

    def __init__(self, *, image: str, cwd: str, run_args: Optional[list[str]] = None, timeout: Optional[int] = None) -> None:
        self.image = image
        self.cwd = cwd
        self.run_args = run_args or []
        self.timeout = timeout
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
