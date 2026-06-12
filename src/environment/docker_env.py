"""Docker environment wrapper.

Wraps mini-swe-agent 1.17.5 DockerEnvironment for launching containers with
writable codebase mounts so agents can modify files and run tests inside the
container.
"""

from __future__ import annotations

import logging
import argparse
import fcntl
import os
import sys
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
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


def remove_docker_image(image: str) -> bool:
    """Best-effort removal for a no-longer-needed Docker image."""
    if not image:
        return False
    try:
        result = subprocess.run(
            ["docker", "image", "rm", image],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError:
        logger.warning("Docker CLI not found; cannot remove image %s", image)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Timed out removing Docker image: %s", image)
        return False

    if result.returncode == 0:
        logger.info("Removed Docker image after instance: %s", image)
        return True

    msg = (result.stderr or result.stdout or "").strip()
    if "No such image" in msg:
        logger.debug("Docker image already absent: %s", image)
    else:
        logger.warning("Failed to remove Docker image %s: %s", image, msg[:500])
    return False


def cleanup_docker_image_cache(max_cached_images: int = 75) -> int:
    """Keep the newest idle project images and protect container references.

    The cleanup is intentionally scoped to images used by this project so it
    does not evict unrelated Docker work on the same machine. Images referenced
    by running or stopped containers are never counted against the idle cache
    limit and are never removed.

    Args:
        max_cached_images: Number of newest project images to retain.

    Returns:
        Number of image references removed.
    """
    referenced_ids = _list_container_image_ids()
    images = [
        image
        for image in _list_project_docker_images()
        if image["id"] not in referenced_ids
    ]
    if len(images) <= max_cached_images:
        logger.info(
            "Docker image cache within limit: %d/%d idle project images "
            "(%d container-referenced image IDs protected)",
            len(images),
            max_cached_images,
            len(referenced_ids),
        )
        return 0

    images.sort(key=lambda item: item["created"], reverse=True)
    stale = images[max_cached_images:]
    removed = 0
    for image in stale:
        removed += remove_docker_image(image["ref"])
    logger.info(
        "Docker image cache cleanup removed %d old idle project images; "
        "retained %d idle images and protected %d referenced image IDs",
        removed,
        max_cached_images,
        len(referenced_ids),
    )
    return removed


def prune_dangling_images() -> None:
    """Delete only untagged images that no container references."""
    try:
        result = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not prune dangling Docker images: %s", exc)
        return
    if result.returncode != 0:
        logger.warning(
            "Docker dangling-image prune failed: "
            f"{(result.stderr or result.stdout)[:500]}"
        )


def prune_build_cache(*, aggressive: bool) -> None:
    """Prune unused BuildKit cache according to the current disk pressure."""
    args = ["docker", "builder", "prune", "-af"]
    if not aggressive:
        args = ["docker", "builder", "prune", "-f", "--filter", "until=24h"]
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not prune Docker build cache: %s", exc)
        return
    if result.returncode != 0:
        logger.warning(
            "Docker build-cache prune failed: "
            f"{(result.stderr or result.stdout)[:500]}"
        )


class DockerCapacityWindow:
    """Shared capacity controller for concurrent Docker workloads.

    All workers using the same window share a bounded container semaphore and
    a maintenance lock. The lock ensures disk checks and image-cache cleanup
    never race across workers.
    """

    def __init__(
        self,
        *,
        max_concurrent: int,
        max_cached_images: int,
        min_free_gb: int,
        disk_path: str | Path = ".",
        lock_dir: str | Path | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        if max_cached_images < max_concurrent:
            raise ValueError(
                "max_cached_images must be at least max_concurrent"
            )
        if min_free_gb < 1:
            raise ValueError("min_free_gb must be at least 1")
        self.max_concurrent = max_concurrent
        self.max_cached_images = max_cached_images
        self.min_free_gb = min_free_gb
        self.disk_path = Path(disk_path)
        self.lock_dir = Path(
            lock_dir
            or os.environ.get(
                "VIBE_DOCKER_WINDOW_DIR",
                "/tmp/vibe-coding-planning-docker-window",
            )
        )
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._maintenance_lock = threading.Lock()
        self._image_acquisition_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._local = threading.local()
        self._active = 0
        self._peak_active = 0

    @contextmanager
    def _interprocess_lock(self, name: str) -> Iterator[None]:
        path = self.lock_dir / name
        with path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _acquire_process_slot(self) -> Any:
        while True:
            for index in range(self.max_concurrent):
                handle = (self.lock_dir / f"slot-{index}.lock").open("a+")
                try:
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    return handle
                except BlockingIOError:
                    handle.close()
            time.sleep(0.1)

    def _acquire_usage_lock(self) -> Any:
        """Hold a shared lock for one complete Docker lifecycle."""
        handle = (self.lock_dir / "usage.lock").open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return handle

    @contextmanager
    def _exclusive_idle_usage(self) -> Iterator[bool]:
        """Acquire the global Docker lifecycle lock only when all users idle."""
        with self._state_lock:
            locally_active = bool(self._active)
        if locally_active:
            yield False
            return
        handle = (self.lock_dir / "usage.lock").open("a+")
        try:
            try:
                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    @staticmethod
    def _release_file_lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    @property
    def active(self) -> int:
        with self._state_lock:
            return self._active

    @property
    def peak_active(self) -> int:
        with self._state_lock:
            return self._peak_active

    def _free_gb(self) -> int:
        return int(shutil.disk_usage(self.disk_path).free / (1024**3))

    def ensure_capacity(self) -> None:
        """Clean the image cache and fail before launch if disk remains low."""
        with self._maintenance_lock:
            with self._interprocess_lock("maintenance.lock"):
                free_gb = self._free_gb()
                if free_gb < self.min_free_gb:
                    logger.warning(
                        "Docker window low disk: %dGiB free; cleaning Docker storage",
                        free_gb,
                    )
                    prune_dangling_images()
                    prune_build_cache(aggressive=True)
                    free_gb = self._free_gb()
                if free_gb < self.min_free_gb:
                    raise FatalError(
                        "Docker capacity window blocked container launch: "
                        f"{free_gb}GiB free < {self.min_free_gb}GiB minimum"
                    )

    @contextmanager
    def image_acquisition(self) -> Iterator[None]:
        """Serialize project image pulls and builds across all workers."""
        with self._image_acquisition_lock:
            with self._interprocess_lock("image-acquisition.lock"):
                yield

    def maintain(self) -> None:
        """Serialize project-image cleanup across all window users."""
        with self._maintenance_lock:
            with self._interprocess_lock("maintenance.lock"):
                with self._exclusive_idle_usage() as all_idle:
                    if all_idle:
                        cleanup_docker_image_cache(self.max_cached_images)
                    else:
                        logger.info(
                            "Docker image eviction deferred while another "
                            "capacity slot is active"
                        )
                    prune_dangling_images()
                    free_gb = self._free_gb()
                    if free_gb < max(self.min_free_gb * 4, 80):
                        prune_build_cache(aggressive=True)
                    elif free_gb < max(self.min_free_gb * 8, 150):
                        prune_build_cache(aggressive=False)

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Acquire one shared Docker slot for a complete container lifecycle."""
        depth = getattr(self._local, "lease_depth", 0)
        if depth:
            self._local.lease_depth = depth + 1
            try:
                yield
            finally:
                self._local.lease_depth -= 1
            return

        self._slots.acquire()
        process_slot = None
        usage_lock = None
        capacity_ready = False
        try:
            process_slot = self._acquire_process_slot()
            usage_lock = self._acquire_usage_lock()
            self.ensure_capacity()
            capacity_ready = True
            self._local.lease_depth = 1
            with self._state_lock:
                self._active += 1
                self._peak_active = max(self._peak_active, self._active)
            try:
                yield
            finally:
                with self._state_lock:
                    self._active -= 1
                self._local.lease_depth = 0
        finally:
            if usage_lock is not None:
                self._release_file_lock(usage_lock)
            if process_slot is not None:
                self._release_file_lock(process_slot)
            self._slots.release()
            if capacity_ready:
                try:
                    self.maintain()
                except Exception:
                    logger.exception(
                        "Docker maintenance failed after lease release"
                    )


_DEFAULT_WINDOW: DockerCapacityWindow | None = None
_DEFAULT_WINDOW_LOCK = threading.Lock()


def configure_docker_capacity(
    config: DockerConfig, *, max_concurrent: int = 1
) -> DockerCapacityWindow:
    """Configure and return the process-wide Docker capacity window."""
    global _DEFAULT_WINDOW
    with _DEFAULT_WINDOW_LOCK:
        desired = (
            max_concurrent,
            config.max_cached_images,
            config.min_free_gb,
        )
        current = _DEFAULT_WINDOW
        current_values = (
            current.max_concurrent,
            current.max_cached_images,
            current.min_free_gb,
        ) if current else None
        if current_values != desired:
            _DEFAULT_WINDOW = DockerCapacityWindow(
                max_concurrent=max_concurrent,
                max_cached_images=config.max_cached_images,
                min_free_gb=config.min_free_gb,
            )
        return _DEFAULT_WINDOW


def get_docker_capacity_window(
    config: DockerConfig | None = None,
) -> DockerCapacityWindow:
    """Return the shared window, creating a single-slot default if needed."""
    if config is not None:
        with _DEFAULT_WINDOW_LOCK:
            global _DEFAULT_WINDOW
            if (
                _DEFAULT_WINDOW is not None
                and _DEFAULT_WINDOW.max_cached_images
                == config.max_cached_images
                and _DEFAULT_WINDOW.min_free_gb == config.min_free_gb
            ):
                return _DEFAULT_WINDOW
        return configure_docker_capacity(config)
    with _DEFAULT_WINDOW_LOCK:
        if _DEFAULT_WINDOW is None:
            _DEFAULT_WINDOW = DockerCapacityWindow(
                max_concurrent=1,
                max_cached_images=75,
                min_free_gb=20,
            )
        return _DEFAULT_WINDOW


@contextmanager
def managed_docker_client(
    *, timeout: int | None = None
) -> Iterator[Any]:
    """Create a Docker SDK client under the shared capacity window."""
    window = get_docker_capacity_window()
    with window.lease():
        import docker

        client = docker.from_env(timeout=timeout) if timeout else docker.from_env()
        try:
            yield client
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()


def create_docker_client(*, timeout: int | None = None) -> Any:
    """Create a Docker SDK client that owns a shared-window lease."""
    lease = get_docker_capacity_window().lease()
    lease.__enter__()
    try:
        import docker

        client = docker.from_env(timeout=timeout) if timeout else docker.from_env()
        setattr(client, "_vibe_capacity_lease", lease)
        return client
    except BaseException:
        lease.__exit__(*sys.exc_info())
        raise


def close_docker_client(client: Any) -> None:
    """Close a client created by :func:`create_docker_client`."""
    try:
        close = getattr(client, "close", None)
        if close is not None:
            close()
    finally:
        lease = getattr(client, "_vibe_capacity_lease", None)
        if lease is not None:
            lease.__exit__(None, None, None)
            delattr(client, "_vibe_capacity_lease")


def run_docker_cli(
    args: list[str],
    *,
    timeout: int = 300,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a Docker CLI command under the shared capacity window."""
    if not args or args[0] != "docker":
        raise ValueError("Docker CLI command must start with 'docker'")
    with get_docker_capacity_window().lease():
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )


def prune_docker_resources(max_cached_images: int = 75) -> None:
    """Prune stopped containers/build cache and enforce the image window."""
    for args in (
        ["docker", "container", "prune", "-f"],
        ["docker", "builder", "prune", "-f"],
    ):
        result = run_docker_cli(args, timeout=300)
        if result.returncode != 0:
            logger.warning("Docker maintenance failed: %s", result.stderr[:500])
    cleanup_docker_image_cache(max_cached_images)


def reset_project_docker_resources() -> None:
    """Stop project containers and remove project images/build cache."""
    result = run_docker_cli(
        ["docker", "container", "ls", "-aq", "--filter", "name=minisweagent-"],
        timeout=120,
    )
    container_ids = result.stdout.split()
    if container_ids:
        remove_result = run_docker_cli(
            ["docker", "container", "rm", "-f", *container_ids],
            timeout=300,
        )
        if remove_result.returncode != 0:
            raise FatalError(
                "Failed to remove project Docker containers: "
                f"{remove_result.stderr[:500]}"
            )
    prune_docker_resources(max_cached_images=0)
    dangling_result = run_docker_cli(
        ["docker", "image", "prune", "-af"],
        timeout=300,
    )
    if dangling_result.returncode != 0:
        raise FatalError(
            "Failed to prune residual Docker images: "
            f"{dangling_result.stderr[:500]}"
        )


def _list_project_docker_images() -> list[dict[str, str]]:
    """Return project image refs with creation timestamps."""
    try:
        result = subprocess.run(
            [
                "docker",
                "image",
                "ls",
                "--no-trunc",
                "--format",
                "{{.Repository}}:{{.Tag}}\t{{.ID}}",
            ],
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


def _list_container_image_ids() -> set[str]:
    """Return image IDs referenced by all running and stopped containers."""
    try:
        result = subprocess.run(
            [
                "docker",
                "container",
                "ls",
                "-aq",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not list Docker containers: %s", exc)
        return set()
    if result.returncode != 0:
        logger.warning(
            "Docker container ls failed: "
            f"{(result.stderr or result.stdout)[:500]}"
        )
        return set()
    container_ids = result.stdout.split()
    if not container_ids:
        return set()
    inspect = subprocess.run(
        [
            "docker",
            "container",
            "inspect",
            "--format",
            "{{.Image}}",
            *container_ids,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if inspect.returncode != 0:
        logger.warning(
            "Docker container inspect failed: "
            f"{(inspect.stderr or inspect.stdout)[:500]}"
        )
        return set()
    return {line.strip() for line in inspect.stdout.splitlines() if line.strip()}


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

    def __init__(
        self,
        docker_config: DockerConfig,
        capacity_window: DockerCapacityWindow | None = None,
    ) -> None:
        """Initialise the wrapper.

        ``docker_config`` is currently unused internally (1.17.5's
        DockerEnvironment receives all settings via constructor kwargs),
        but the parameter is retained for API compatibility with existing
        tests and callers.
        """
        self._config = docker_config
        self._capacity_window = capacity_window or get_docker_capacity_window(
            docker_config
        )
        self._lease_context: Any = None
        self._env: Any = None
        self._image: str = ""

    def start(
        self,
        image: str,
        workdir: str,
        mount_source: str | None = None,
        timeout: int | None = None,
        instance_info: dict[str, Any] | None = None,
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
        self._lease_context = self._capacity_window.lease()
        self._lease_context.__enter__()
        try:
            DockerEnvironment = _import_docker_env()
            image = _resolve_polybench_image(
                image,
                timeout=self._config.polybench_pull_timeout,
                instance_info=instance_info,
                build_fallback=self._config.polybench_build_fallback,
                build_timeout=self._config.polybench_build_timeout,
                capacity_window=self._capacity_window,
            )
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
        except BaseException:
            self._lease_context.__exit__(*sys.exc_info())
            self._lease_context = None
            raise

    def stop(self) -> None:
        """Stop and remove the running container.

        Under 1.17.5 this calls ``cleanup()`` on the underlying
        ``DockerEnvironment``.
        """
        if self._env is not None:
            logger.info("Stopping Docker container")
            self._env.cleanup()
            self._env = None
        if self._lease_context is not None:
            self._lease_context.__exit__(None, None, None)
            self._lease_context = None

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


def _main() -> int:
    parser = argparse.ArgumentParser(description="Central Docker maintenance")
    parser.add_argument("command", choices=("maintain", "reset-project"))
    parser.add_argument("--max-cached-images", type=int, default=75)
    parser.add_argument("--max-concurrent", type=int, default=1)
    parser.add_argument("--min-free-gb", type=int, default=20)
    args = parser.parse_args()
    if args.command == "maintain":
        window = DockerCapacityWindow(
            max_concurrent=args.max_concurrent,
            max_cached_images=args.max_cached_images,
            min_free_gb=args.min_free_gb,
        )
        window.maintain()
    elif args.command == "reset-project":
        reset_project_docker_resources()
    return 0


def _resolve_polybench_image(
    image: str,
    timeout: int | None = None,
    *,
    instance_info: dict[str, Any] | None = None,
    build_fallback: bool = False,
    build_timeout: int = 3600,
    capacity_window: DockerCapacityWindow | None = None,
) -> str:
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

    for candidate in candidates:
        if _docker_image_exists(candidate):
            if candidate != image:
                logger.info(
                    "Using PolyBench fallback image already local: %s",
                    candidate,
                )
            return candidate

    window = capacity_window or get_docker_capacity_window()
    pull_timeout = timeout or 1800
    last_error = ""
    with window.image_acquisition():
        # Another worker may have completed the image while this worker waited.
        for candidate in candidates:
            if _docker_image_exists(candidate):
                if candidate != image:
                    logger.info(
                        "Using PolyBench fallback image already local: %s",
                        candidate,
                    )
                return candidate

        for candidate in candidates:
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
                logger.info(
                    "PolyBench image pull failed for %s: %s",
                    candidate,
                    last_error,
                )

        if build_fallback and instance_info:
            logger.info(
                "GHCR image unavailable; building with the official "
                "PolyBench Dockerfile"
            )
            from src.environment.polybench_image import (
                build_polybench_image_from_official_dockerfile,
            )

            return build_polybench_image_from_official_dockerfile(
                instance_info,
                build_timeout=build_timeout,
            )

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


if __name__ == "__main__":
    raise SystemExit(_main())
