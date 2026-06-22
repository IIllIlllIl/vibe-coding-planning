"""Apptainer environment backend for GEPA on HPC.

Provides an environment object compatible with mini-swe-agent 1.17.5's
DockerEnvironment public surface (``execute``, ``get_template_vars``,
``cleanup``) while running containers via Apptainer/Singularity ``.sif``
images instead of Docker.
"""

from __future__ import annotations

import base64
import logging
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from src.environment.docker_env import DockerCapacityWindow
from src.exceptions import FatalError

logger = logging.getLogger(__name__)


def _image_to_sif_name(image: str) -> str:
    """Map a Docker image reference to a safe SIF file name.

    Example:
        ``swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest``
        ->
        ``swebench_sweb.eval.x86_64.astropy_1776_astropy-12907_latest.sif``
    """
    safe = image.replace("/", "_").replace(":", "_")
    # Remove characters that are illegal or suspicious in file names.
    safe = "".join(c for c in safe if c.isalnum() or c in "._-")
    return f"{safe}.sif"


class ApptainerSifCache:
    """Manage a local cache of Apptainer SIF images derived from Docker refs."""

    def __init__(
        self,
        sif_cache_dir: Path,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.sif_cache_dir = Path(sif_cache_dir)
        self.sif_cache_dir.mkdir(parents=True, exist_ok=True)
        self._capacity_window = capacity_window

    def sif_path(self, image: str) -> Path:
        return self.sif_cache_dir / _image_to_sif_name(image)

    def ensure(self, image: str, *, timeout: int | None = 600) -> Path:
        """Return the local SIF path, pulling from Docker Hub if missing.

        Pulls are serialized across workers via the shared image-acquisition
        lock so that concurrent GEPA workers do not race on the same SIF.
        If the cache filesystem does not have enough free space, the pull is
        aborted before consuming disk.
        """
        sif = self.sif_path(image)
        if sif.exists():
            return sif

        free_gb = int(shutil.disk_usage(self.sif_cache_dir).free / (1024**3))
        min_gb = self._capacity_window.min_free_gb
        if free_gb < min_gb:
            raise FatalError(
                f"Apptainer SIF cache low on disk: {free_gb}GiB free "
                f"< {min_gb}GiB minimum. Refusing to pull {image}."
            )

        with self._capacity_window.image_acquisition():
            # Another worker may have completed the pull while we waited.
            if sif.exists():
                return sif

            self.sif_cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                "Apptainer: pulling SIF for %s -> %s",
                image,
                sif,
            )
            try:
                result = subprocess.run(
                    [
                        "apptainer",
                        "pull",
                        "--force",
                        str(sif),
                        f"docker://{image}",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise FatalError(
                    f"Apptainer SIF pull timed out after {exc.timeout}s: {image}"
                ) from exc
            except FileNotFoundError as exc:
                raise FatalError(
                    "Apptainer CLI not found. "
                    "Load the Apptainer module before running, e.g. "
                    "'module load tools/Apptainer'."
                ) from exc

            if result.returncode != 0:
                stderr = (result.stderr or result.stdout or "").strip()
                raise FatalError(
                    f"Apptainer pull failed for {image}: {stderr[:1000]}"
                )

        if not sif.exists():
            raise FatalError(
                f"Apptainer pull reported success but SIF is missing: {sif}"
            )
        return sif


class ApptainerEnvironment:
    """Apptainer-backed execution environment for mini-swe-agent agents.

    Mirrors the mini-swe-agent 1.17.5 ``DockerEnvironment`` public surface:

    - ``execute(command, cwd=\"\", *, timeout=None) -> dict[str, Any]``
    - ``get_template_vars() -> dict[str, Any]``
    - ``cleanup() -> None``

    The SIF image is expected to exist before construction (use
    :class:`ApptainerSifCache` to pull it). Construction acquires one slot
    from the shared :class:`DockerCapacityWindow`; cleanup releases it.
    """

    def __init__(
        self,
        image: str,
        cwd: str,
        *,
        sif_cache_dir: Path,
        capacity_window: DockerCapacityWindow,
        run_args: list[str] | None = None,
        timeout: int | None = None,
        container_timeout: str = "4h",
        writable_tmpfs: bool = True,
        network_disabled: bool = False,
        git_safe_directories: list[str] | None = None,
    ) -> None:
        self._image = image
        self._cwd = cwd
        self._run_args = list(run_args or [])
        self._timeout = timeout
        self._container_timeout = container_timeout
        self._writable_tmpfs = writable_tmpfs
        self._network_disabled = network_disabled
        self._git_safe_dirs = list(git_safe_directories or [cwd])
        self._git_config_path = "/tmp/vibe_gitconfig"

        self._cache = ApptainerSifCache(sif_cache_dir, capacity_window)
        # Pull the SIF on demand if it is not already cached. This lets a GEPA
        # job start from a partial cache without requiring a full preheat pass.
        self._sif_path = self._cache.ensure(
            image, timeout=self._timeout if self._timeout is not None else 1800
        )

        self._capacity_window = capacity_window
        self._lease: Any = None
        self._lease = capacity_window.lease()
        self._lease.__enter__()
        try:
            self._ensure_git_config()
        except BaseException:
            self._lease.__exit__(*sys.exc_info())
            self._lease = None
            raise

    def _ensure_git_config(self) -> None:
        """Create a temporary gitconfig inside the container that trusts cwd."""
        lines = ["[safe]"]
        for directory in self._git_safe_dirs:
            lines.append(f"\tdirectory = {directory}")
        content = "\n".join(lines) + "\n"
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # Create the config file in the same apptainer invocation so it works
        # regardless of whether $HOME is bind-mounted from the host.
        self.execute(
            f"mkdir -p /tmp && echo {shlex.quote(encoded)} | base64 -d > "
            f"{self._git_config_path}",
            timeout=30,
        )

    def _build_args(
        self,
        cwd: str,
        command: str,
        timeout: int | None,
    ) -> list[str]:
        args: list[str] = ["apptainer", "exec"]
        if self._writable_tmpfs:
            args.append("--writable-tmpfs")
        if self._network_disabled:
            args.extend(["--net", "--network", "none"])
        args.extend(self._run_args)
        args.extend(["--env", f"GIT_CONFIG_GLOBAL={self._git_config_path}"])
        args.append(str(self._sif_path))
        # container_timeout is kept for API compatibility with DockerEnvironment;
        # Apptainer exec does not expose an equivalent flag, so we rely on the
        # subprocess timeout enforced by execute().
        _ = self._container_timeout
        args.extend(
            [
                "bash",
                "-lc",
                f"cd {shlex.quote(cwd)} && {command}",
            ]
        )
        return args

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a shell command inside the Apptainer container."""
        actual_cwd = cwd or self._cwd
        effective_timeout = timeout if timeout is not None else self._timeout
        args = self._build_args(actual_cwd, command, effective_timeout)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=effective_timeout,
            )
        except FileNotFoundError as exc:
            raise FatalError(
                "Apptainer CLI not found. "
                "Load the Apptainer module before running, e.g. "
                "'module load tools/Apptainer'."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FatalError(
                f"Apptainer command timed out after {exc.timeout}s: {command[:200]}"
            ) from exc

        return {
            "output": (result.stdout or "") + (result.stderr or ""),
            "returncode": result.returncode,
        }

    def get_template_vars(self) -> dict[str, Any]:
        """Return template variables for prompt rendering."""
        return {"cwd": self._cwd}

    def cleanup(self) -> None:
        """Release the capacity-window slot.

        Apptainer exec containers are ephemeral; there is no persistent
        container or overlay to remove.
        """
        if self._lease is not None:
            self._lease.__exit__(None, None, None)
            self._lease = None
