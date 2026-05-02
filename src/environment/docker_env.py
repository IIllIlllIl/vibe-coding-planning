"""Docker environment wrapper.

Wraps mini-swe-agent's DockerEnvironment for launching containers with
read-only codebase mounts.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config import DockerConfig
from src.exceptions import FatalError

logger = logging.getLogger(__name__)


def _import_docker_env() -> type:
    """Lazy import DockerEnvironment to fail gracefully if not installed."""
    try:
        from minisweagent import DockerEnvironment  # type: ignore[import-untyped]

        return DockerEnvironment  # type: ignore[return-value]
    except ImportError as exc:
        raise FatalError(
            "mini-swe-agent is not installed. "
            "Please install it: pip install mini-swe-agent~=1.0"
        ) from exc


class DockerEnvWrapper:
    """Wrapper around mini-swe-agent DockerEnvironment.

    Usage:
        env = DockerEnvWrapper(docker_config)
        env.start(image="swebench/astropy:latest", workdir="/testbed")
        result = env.execute("ls /testbed")
        env.stop()
    """

    def __init__(self, docker_config: DockerConfig) -> None:
        self._config = docker_config
        self._docker_env_class: type | None = None
        self._env: Any = None

    def start(
        self,
        image: str,
        workdir: str,
        ro_mount_source: str | None = None,
    ) -> None:
        """Start a Docker container.

        Args:
            image: Docker image name and tag.
            workdir: Working directory inside the container.
            ro_mount_source: Host path to mount read-only into the container
                at ``workdir``. If None, no extra mount is configured.
        """
        DockerEnvironment = _import_docker_env()
        self._docker_env_class = DockerEnvironment

        run_args: list[str] = []
        if ro_mount_source is not None:
            # Mount source as read-only at the workdir
            run_args.extend([
                "--mount",
                f"type=bind,source={ro_mount_source},target={workdir},readonly",
            ])

        self._env = DockerEnvironment(
            image=image,
            workdir=workdir,
            run_args=run_args,
        )
        logger.info("Starting Docker container: image=%s workdir=%s", image, workdir)
        self._env.start()

    def stop(self) -> None:
        """Stop and remove the running container."""
        if self._env is not None:
            logger.info("Stopping Docker container")
            self._env.stop()
            self._env = None

    def execute(self, command: str) -> str:
        """Execute a shell command inside the running container.

        Args:
            command: Shell command to execute.

        Returns:
            Command stdout as a string.

        Raises:
            FatalError: If no container is running.
        """
        if self._env is None:
            raise FatalError("Docker environment not started. Call start() first.")
        return self._env.execute(command)

    def __enter__(self) -> "DockerEnvWrapper":
        """Context manager entry (start() must be called separately)."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit – always stop the container."""
        self.stop()
