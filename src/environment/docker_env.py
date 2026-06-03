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
_PROJECT_IMAGE_PREFIXES = (
    "swebench/sweb.eval.x86_64.",
    _POLYBENCH_GHCR_PREFIX,
    "jefzda/sweap-images:",
    "polybench_",
)


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


def is_docker_storage_error(text: str) -> bool:
    """Return True for Docker errors that indicate host storage corruption/fullness."""
    lowered = text.lower()
    patterns = (
        "no space left on device",
        "input/output error",
        "/var/lib/desktop-containerd",
        "containerd.metadata",
        "meta.db",
    )
    return any(pattern in lowered for pattern in patterns)


def remove_docker_image(image: str) -> None:
    """Best-effort removal for a no-longer-needed Docker image."""
    if not image:
        return
    try:
        result = subprocess.run(
            ["docker", "image", "rm", "-f", image],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError:
        logger.warning("Docker CLI not found; cannot remove image %s", image)
        return
    except subprocess.TimeoutExpired:
        logger.warning("Timed out removing Docker image: %s", image)
        return

    if result.returncode == 0:
        logger.info("Removed Docker image after instance: %s", image)
        return

    msg = (result.stderr or result.stdout or "").strip()
    if "No such image" in msg:
        logger.debug("Docker image already absent: %s", image)
    else:
        logger.warning("Failed to remove Docker image %s: %s", image, msg[:500])


def cleanup_docker_image_cache(max_cached_images: int = 75) -> int:
    """Keep only the newest project-related Docker images.

    The cleanup is intentionally scoped to images used by this project so it
    does not evict unrelated Docker work on the same machine.

    Args:
        max_cached_images: Number of newest project images to retain.

    Returns:
        Number of image references removed.
    """
    images = _list_project_docker_images()
    if len(images) <= max_cached_images:
        logger.info(
            "Docker image cache within limit: %d/%d project images",
            len(images),
            max_cached_images,
        )
        return 0

    images.sort(key=lambda item: item["created"], reverse=True)
    stale = images[max_cached_images:]
    removed = 0
    for image in stale:
        remove_docker_image(image["ref"])
        removed += 1
    logger.info(
        "Docker image cache cleanup removed %d old project images; retained %d",
        removed,
        max_cached_images,
    )
    return removed


def _list_project_docker_images() -> list[dict[str, str]]:
    """Return project image refs with creation timestamps."""
    try:
        result = subprocess.run(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}\t{{.ID}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not list Docker images for cache cleanup: %s", exc)
        return []

    if result.returncode != 0:
        logger.warning("Docker image ls failed: %s", (result.stderr or result.stdout)[:500])
        return []

    images: list[dict[str, str]] = []
    seen_refs: set[str] = set()
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        ref, image_id = line.split("\t", 1)
        if ref in seen_refs or ref.startswith("<none>:"):
            continue
        if not _is_project_image_ref(ref):
            continue
        seen_refs.add(ref)
        created = _inspect_image_created(ref)
        images.append({"ref": ref, "id": image_id, "created": created})
    return images


def _is_project_image_ref(ref: str) -> bool:
    return any(ref.startswith(prefix) for prefix in _PROJECT_IMAGE_PREFIXES)


def _inspect_image_created(ref: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", ref, "--format", "{{.Created}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


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
        self._image: str = ""

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
        self._image = image

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

    @property
    def image(self) -> str:
        """Return the concrete image used for the current/last container."""
        return self._image

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
            if is_docker_storage_error(last_error):
                raise FatalError(
                    "Docker storage error while pulling PolyBench image. "
                    "Stop the batch and free Docker disk space before retrying. "
                    f"Image={candidate}. Error: {last_error}"
                ) from exc
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
