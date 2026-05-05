"""Docker environment wrapper.

Wraps mini-swe-agent 1.17.5 DockerEnvironment for launching containers with
writable codebase mounts so agents can modify files and run tests inside the
container.
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
        from minisweagent.environments.docker import DockerEnvironment  # type: ignore[import-untyped]

        return DockerEnvironment  # type: ignore[return-value]
    except ImportError as exc:
        raise FatalError(
            "mini-swe-agent is not installed. "
            "Please install it: pip install mini-swe-agent>=1.17.5"
        ) from exc


class DockerEnvWrapper:
    """Wrapper around mini-swe-agent 1.17.5 DockerEnvironment.

    1.17.5 DockerEnvironment starts the container immediately upon
    construction (no explicit ``start()`` method).  Cleanup is via
    :meth:`cleanup`.  This wrapper preserves the old ``start()`` /
    ``stop()`` surface so that callers (``pipeline.py``, tests) do not
    need to change.

    Usage::

        env = DockerEnvWrapper(docker_config)
        env.start(image="swebench/astropy:latest", workdir="/testbed")
        result = env.execute("ls /testbed")
        env.stop()
    """

    def __init__(self, docker_config: DockerConfig) -> None:
        """Initialise the wrapper.

        ``docker_config`` is currently unused internally (1.17.5's
        DockerEnvironment receives all settings via constructor kwargs),
        but the parameter is retained for API compatibility with existing
        tests and callers.
        """
        self._config = docker_config
        self._env: Any = None

    def start(
        self,
        image: str,
        workdir: str,
        ro_mount_source: str | None = None,
    ) -> None:
        """Start a Docker container.

        Under 1.17.5 this constructs a ``DockerEnvironment`` which
        launches the container immediately.

        Args:
            image: Docker image name and tag.
            workdir: Working directory inside the container (maps to
                ``cwd`` in 1.17.5).
            ro_mount_source: Host path to mount into the container at
                ``workdir``. If None, no extra mount is configured.
                The mount is writable so agents can modify files.
        """
        DockerEnvironment = _import_docker_env()

        kwargs: dict[str, Any] = {
            "image": image,
            "cwd": workdir,
        }
        if ro_mount_source:
            kwargs["run_args"] = [
                "--rm",
                "--mount",
                f"type=bind,source={ro_mount_source},target={workdir}",
            ]

        self._env = DockerEnvironment(**kwargs)
        logger.info("Docker container started: image=%s cwd=%s", image, workdir)

    def stop(self) -> None:
        """Stop and remove the running container.

        Under 1.17.5 this calls ``cleanup()`` on the underlying
        ``DockerEnvironment``.
        """
        if self._env is not None:
            logger.info("Stopping Docker container")
            self._env.cleanup()
            self._env = None

    def execute(self, command: str) -> dict[str, Any]:
        """Execute a shell command inside the running container.

        Args:
            command: Shell command to execute.

        Returns:
            A dict with at least ``{"output": str, "returncode": int}``.
            This matches mini-swe-agent 1.17.5's DockerEnvironment
            return type so that DefaultAgent can merge it with action
            metadata (``output | {"action": ...}``).

        Raises:
            FatalError: If no container is running.
        """
        if self._env is None:
            raise FatalError("Docker environment not started. Call start() first.")
        result = self._env.execute(command)
        # 1.17.5 already returns dict; be defensive for mocks / future changes
        if isinstance(result, dict):
            return result
        return {"output": str(result), "returncode": 0}

    def get_template_vars(self) -> dict[str, Any]:
        """Return template variables from the underlying environment.

        DefaultAgent.render_template() calls this to inject environment-
        specific variables into prompt templates.

        Returns:
            Template variable dict, or empty dict if not started.
        """
        if self._env is None:
            return {}
        return self._env.get_template_vars()

    def __enter__(self) -> "DockerEnvWrapper":
        """Context manager entry (start() must be called separately)."""
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit – always stop the container."""
        self.stop()
