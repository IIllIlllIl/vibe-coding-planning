"""Docker environment wrapper.

Wraps mini-swe-agent 1.17.5 DockerEnvironment for launching containers with
writable codebase mounts so agents can modify files and run tests inside the
container.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from src.config import DockerConfig
from src.exceptions import FatalError

logger = logging.getLogger(__name__)

_POLYBENCH_GHCR_PREFIX = "ghcr.io/timesler/swe-polybench.eval.x86_64."
_POLYBENCH_IMAGE_TAGS = ("v1.1", "v1.0", "latest")


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
        mount_source: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Start a Docker container.

        Under 1.17.5 this constructs a ``DockerEnvironment`` which
        launches the container immediately.

        Args:
            image: Docker image name and tag.
            workdir: Working directory inside the container (maps to
                ``cwd`` in 1.17.5).
            mount_source: Host path to mount into the container at
                ``workdir``. If None, no extra mount is configured.
                The mount is writable so agents can modify files.
            timeout: Per-command execution timeout in seconds forwarded
                to ``DockerEnvironment``.
        """
        DockerEnvironment = _import_docker_env()
        image = _resolve_polybench_image(image, timeout=timeout)

        kwargs: dict[str, Any] = {
            "image": image,
            "cwd": workdir,
            "container_timeout": "4h",
        }
        if mount_source:
            kwargs["run_args"] = [
                "--rm",
                "--mount",
                f"type=bind,source={mount_source},target={workdir}",
            ]
        if timeout is not None:
            kwargs["timeout"] = timeout

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


def _resolve_polybench_image(image: str, timeout: int | None = None) -> str:
    """Ensure PolyBench GHCR images are local, with tag fallback.

    mini-swe-agent starts containers with ``docker run``. If the image is not
    local, Docker may pull layers during ``docker run`` and hit its startup
    timeout. Pulling explicitly here also lets us handle official PolyBench
    images that exist as ``v1.0``/``latest`` but not ``v1.1``.
    """
    if not image.startswith(_POLYBENCH_GHCR_PREFIX) or ":" not in image:
        return image

    base = image.rsplit(":", 1)[0]
    candidates = [f"{base}:{tag}" for tag in _POLYBENCH_IMAGE_TAGS]
    if image not in candidates:
        candidates.insert(0, image)

    pull_timeout = timeout or 1800
    last_error = ""
    for candidate in candidates:
        if _docker_image_exists(candidate):
            if candidate != image:
                logger.info("Using PolyBench fallback image already local: %s", candidate)
            return candidate
        try:
            logger.info("Pulling PolyBench image: %s", candidate)
            subprocess.run(
                ["docker", "pull", candidate],
                check=True,
                capture_output=True,
                text=True,
                timeout=pull_timeout,
            )
            if candidate != image:
                logger.info("Using PolyBench fallback image: %s", candidate)
            return candidate
        except subprocess.TimeoutExpired as exc:
            last_error = f"timeout after {exc.timeout}s pulling {candidate}"
            logger.warning("PolyBench image pull timed out: %s", candidate)
        except subprocess.CalledProcessError as exc:
            last_error = (exc.stderr or exc.stdout or str(exc)).strip()
            logger.info("PolyBench image pull failed for %s: %s", candidate, last_error)

    raise FatalError(
        "Unable to obtain PolyBench Docker image for agent container. "
        f"Tried: {', '.join(candidates)}. Last error: {last_error}"
    )


def _docker_image_exists(image: str) -> bool:
    """Return True if Docker already has ``image`` locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0
