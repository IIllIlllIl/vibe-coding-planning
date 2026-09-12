"""Apptainer environment backend for GEPA on HPC.

Provides an environment object compatible with mini-swe-agent 1.17.5's
DockerEnvironment public surface (``execute``, ``get_template_vars``,
``cleanup``) while running containers via Apptainer/Singularity ``.sif``
images instead of Docker.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.environment.docker_env import DockerCapacityWindow
from src.exceptions import CommandTimeoutError, FatalError

logger = logging.getLogger(__name__)

_HTTP_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_PYTHON_HTTP_MARKERS = (
    "requests.",
    "httpx.",
    "urllib.request",
    "urlopen(",
    "http.client",
)


def _canonical_http_url(value: str) -> str:
    """Canonicalize only the stable, exact-match parts of an HTTP URL."""

    parsed = urlsplit(value.rstrip(".,;:)]}"))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"not an HTTP URL: {value}")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, "")
    )


def _blocked_git_remote_operation(command: str) -> str | None:
    """Return the disallowed remote Git operation in an Agent shell command."""

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        # Let bash report malformed quoting; this policy is not a shell parser.
        return None

    separators = {";", "&&", "||", "|", "&", "(", ")"}
    start = 0
    for end in range(len(tokens) + 1):
        if end < len(tokens) and tokens[end] not in separators:
            continue
        segment = tokens[start:end]
        start = end + 1
        for index, token in enumerate(segment):
            if Path(token).name != "git":
                continue
            args = segment[index + 1 :]
            cursor = 0
            while cursor < len(args):
                arg = args[cursor]
                if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
                    cursor += 2
                    continue
                if arg.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
                    cursor += 1
                    continue
                if arg.startswith("-"):
                    cursor += 1
                    continue
                if arg in {"clone", "fetch", "pull", "ls-remote"}:
                    return arg
                remaining = args[cursor + 1 :]
                if arg == "remote":
                    remote_action = next(
                        (item for item in remaining if not item.startswith("-")),
                        None,
                    )
                    if remote_action == "update":
                        return "remote update"
                if arg == "submodule" and "--remote" in remaining:
                    submodule_action = next(
                        (item for item in remaining if not item.startswith("-")),
                        None,
                    )
                    if submodule_action == "update":
                        return "submodule update --remote"
                break
    return None


def _blocked_network_source_operation(
    command: str,
    *,
    allowed_urls: frozenset[str],
    target_packages: frozenset[str],
) -> str | None:
    """Return why a common Agent source-acquisition command is disallowed.

    This small command-level policy is an experimental guardrail, not a
    security sandbox. It covers acquisition paths observed in prior PCE
    trajectories and rejects dynamic HTTP requests that cannot be compared
    with the frozen task allowlist.
    """

    remote_git = _blocked_git_remote_operation(command)
    if remote_git is not None:
        return f"remote Git operation 'git {remote_git}'"

    lower = command.lower()
    pip_install = bool(
        re.search(
            r"(?:^|[;&|()]|\s)(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?"
            r"(?:[^\s;&|()]*/)?pip(?:3)?\s+install(?:\s|$)",
            lower,
        )
    )
    if pip_install:
        if re.search(r"(?:git\+|https?://)", command, re.IGNORECASE):
            return "pip installation from a remote URL or VCS source"
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        normalized_tokens = {
            re.sub(
                r"[-_.]+",
                "-",
                re.split(r"[<>=!~;@]", token.split("[", 1)[0], maxsplit=1)[0].lower(),
            )
            for token in tokens
            if token and not token.startswith("-")
        }
        if normalized_tokens & target_packages:
            package = sorted(normalized_tokens & target_packages)[0]
            return f"installation or upgrade of target package '{package}'"

    is_curl_or_wget = bool(
        re.search(
            r"(?:^|[;&|()]|\s)(?:[^\s;&|()]*/)?(?:curl|wget)(?:\s|$)",
            lower,
        )
    )
    is_python_http = any(marker in lower for marker in _PYTHON_HTTP_MARKERS)
    if is_curl_or_wget or is_python_http:
        if (
            re.search(r"(?:^|[;&|()]|\s)(?:[^\s;&|()]*/)?wget(?:\s|$)", lower)
            or is_python_http
        ):
            return (
                "HTTP client cannot enforce this experiment's no-redirect "
                "boundary; use curl without -L for an exact task URL"
            )
        urls = _HTTP_URL_RE.findall(command)
        if not urls:
            return "HTTP request with a dynamic or non-auditable URL"
        for url in urls:
            try:
                canonical = _canonical_http_url(url)
            except ValueError:
                return f"malformed HTTP URL '{url}'"
            if canonical not in allowed_urls:
                return f"HTTP URL is absent from the frozen task allowlist: {canonical}"
        if re.search(r"(?:^|\s)(?:-L|--location)(?:\s|$)", command):
            return "HTTP redirect following is outside the exact-URL allowlist"

    return None


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
            tmp_sif = sif.with_name(f"{sif.name}.tmp.{os.getpid()}")
            tmp_sif.unlink(missing_ok=True)
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
                        str(tmp_sif),
                        f"docker://{image}",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                tmp_sif.unlink(missing_ok=True)
                raise FatalError(
                    f"Apptainer SIF pull timed out after {exc.timeout}s: {image}"
                ) from exc
            except FileNotFoundError as exc:
                tmp_sif.unlink(missing_ok=True)
                raise FatalError(
                    "Apptainer CLI not found. "
                    "Load the Apptainer module before running, e.g. "
                    "'module load tools/Apptainer'."
                ) from exc

            if result.returncode != 0:
                tmp_sif.unlink(missing_ok=True)
                stderr = (result.stderr or result.stdout or "").strip()
                raise FatalError(f"Apptainer pull failed for {image}: {stderr[:1000]}")
            if not tmp_sif.exists():
                tmp_sif.unlink(missing_ok=True)
                raise FatalError(
                    "Apptainer pull reported success but temporary SIF is "
                    f"missing: {tmp_sif}"
                )
            tmp_sif.replace(sif)

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
        host_workdir: Path | None = None,
        initialize_host_workdir: bool = True,
        isolate_tmp: bool = False,
        block_git_remote_operations: bool = False,
        source_access_allowed_urls: list[str] | None = None,
        source_access_target_packages: list[str] | None = None,
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
        self._container_home = "/tmp/vibe_home"
        self._isolated_home: tempfile.TemporaryDirectory[str] | None = None
        self._host_workdir = Path(host_workdir) if host_workdir is not None else None
        self._initialize_host_workdir = initialize_host_workdir
        self._isolate_tmp = isolate_tmp
        self._block_git_remote_operations = block_git_remote_operations
        self._source_access_allowed_urls = frozenset(
            _canonical_http_url(value) for value in (source_access_allowed_urls or [])
        )
        self._source_access_target_packages = frozenset(
            re.sub(r"[-_.]+", "-", value.lower())
            for value in (source_access_target_packages or [])
        )
        self._source_access_policy_enabled = (
            source_access_allowed_urls is not None
            or source_access_target_packages is not None
        )
        self._isolated_tmp: tempfile.TemporaryDirectory[str] | None = None

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
            self._prepare_isolated_home()
            if self._isolate_tmp:
                self._prepare_isolated_tmp()
            if self._host_workdir is not None:
                self._prepare_host_workdir()
            self._ensure_git_config()
        except BaseException:
            if self._isolated_home is not None:
                self._isolated_home.cleanup()
                self._isolated_home = None
            if self._isolated_tmp is not None:
                self._isolated_tmp.cleanup()
                self._isolated_tmp = None
            self._lease.__exit__(*sys.exc_info())
            self._lease = None
            raise

    def _prepare_isolated_home(self) -> None:
        """Set a phase-local writable HOME without exposing the real home."""
        self._isolated_home = tempfile.TemporaryDirectory(prefix="vibe-apptainer-home-")
        self._run_args.extend(
            [
                "--home",
                f"{self._isolated_home.name}:{self._container_home}",
            ]
        )

    def _prepare_isolated_tmp(self) -> None:
        """Bind one persistent /tmp for this Agent phase only.

        ``execute()`` starts a fresh ``apptainer exec`` for every tool action.
        A phase-local host directory keeps temporary files visible between
        actions in one phase without exposing them to later Agent phases.
        """
        self._isolated_tmp = tempfile.TemporaryDirectory(prefix="vibe-apptainer-tmp-")
        self._run_args.extend(["--bind", f"{self._isolated_tmp.name}:/tmp"])

    def _prepare_host_workdir(self) -> None:
        """Create a persistent host workdir and bind it to the container cwd.

        This provides Docker-like statefulness for one agent phase: every
        ``execute()`` call sees the same ``/testbed`` files because changes are
        written to the host workdir. The workdir is intentionally phase-local;
        callers should create a new directory for each Plan/Code/Evaluator
        phase when they want artifact-only isolation between phases.
        """
        assert self._host_workdir is not None
        self._host_workdir.mkdir(parents=True, exist_ok=True)
        if self._initialize_host_workdir and not any(self._host_workdir.iterdir()):
            self._copy_container_cwd_to_host_workdir()
        self._run_args.extend(
            [
                "--bind",
                f"{self._host_workdir}:{self._cwd}",
            ]
        )

    def _copy_container_cwd_to_host_workdir(self) -> None:
        assert self._host_workdir is not None
        assert self._isolated_home is not None
        source = subprocess.Popen(
            [
                "apptainer",
                "exec",
                "--cleanenv",
                "--home",
                f"{self._isolated_home.name}:{self._container_home}",
                str(self._sif_path),
                "bash",
                "-lc",
                f"cd {shlex.quote(self._cwd)} && tar -cf - .",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        assert source.stdout is not None
        try:
            target = subprocess.run(
                ["tar", "-xf", "-", "-C", str(self._host_workdir)],
                stdin=source.stdout,
                capture_output=True,
                check=False,
            )
        finally:
            source.stdout.close()
        source_stderr = source.stderr.read() if source.stderr is not None else b""
        source_returncode = source.wait()
        if source_returncode != 0 or target.returncode != 0:
            raise FatalError(
                "Failed to initialize Apptainer host workdir from image "
                f"{self._image}: "
                f"source_rc={source_returncode} target_rc={target.returncode} "
                f"source_stderr={source_stderr.decode('utf-8', 'replace')[:500]} "
                f"target_stderr={target.stderr.decode('utf-8', 'replace')[:500]}"
            )

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
        )

    def _build_args(
        self,
        cwd: str,
        command: str,
        timeout: int | None,
    ) -> list[str]:
        # Apptainer otherwise inherits the submitting user's environment and
        # binds their home directory.  That lets host-side executables and
        # Python packages (notably ~/.local/bin and ~/.local/lib) override the
        # frozen image, so the same SIF can evaluate differently as the login
        # environment changes.  Keep the image's environment authoritative.
        args: list[str] = ["apptainer", "exec", "--cleanenv"]
        if self._writable_tmpfs:
            args.append("--writable-tmpfs")
        if self._network_disabled:
            args.extend(["--net", "--network", "none"])
        args.extend(self._run_args)
        args.extend(
            [
                "--env",
                f"GIT_CONFIG_GLOBAL={self._git_config_path}",
            ]
        )
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
        blocked_reason = None
        if self._source_access_policy_enabled:
            blocked_reason = _blocked_network_source_operation(
                command,
                allowed_urls=self._source_access_allowed_urls,
                target_packages=self._source_access_target_packages,
            )
        elif self._block_git_remote_operations:
            blocked_operation = _blocked_git_remote_operation(command)
            if blocked_operation is not None:
                blocked_reason = f"remote Git operation 'git {blocked_operation}'"
        if blocked_reason is not None:
            message = (
                "Command blocked by the experiment source boundary: "
                f"{blocked_reason}. Use the repository at the frozen base "
                "commit or an exact URL supplied by the task.\n"
            )
            return {
                "output": message,
                "stdout": "",
                "stderr": message,
                "returncode": 126,
            }
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
            raise CommandTimeoutError(command, exc.timeout) from exc

        return {
            "output": (result.stdout or "") + (result.stderr or ""),
            # Keep the legacy combined stream for mini-swe-agent's interactive
            # observations, while exposing separated authorities to callers
            # that consume machine-readable artifacts.
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
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
        if self._isolated_home is not None:
            self._isolated_home.cleanup()
            self._isolated_home = None
        if self._isolated_tmp is not None:
            self._isolated_tmp.cleanup()
            self._isolated_tmp = None
