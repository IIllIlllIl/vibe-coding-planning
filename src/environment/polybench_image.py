"""PolyBench image acquisition using the official evaluation package."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from src.environment.docker_env import (
    close_docker_client,
    create_docker_client,
    run_docker_cli,
)
from src.exceptions import FatalError

logger = logging.getLogger(__name__)

_BUSTER_IMAGES = (
    "python:3.8.16-slim-buster",
    "public.ecr.aws/docker/library/python:3.8.16-slim-buster",
)
_FLOATING_PYTHON_310_IMAGES = (
    "python:3.10-slim",
    "public.ecr.aws/docker/library/python:3.10-slim",
)
_BUSTER_ARCHIVE_SETUP = """RUN sed -i \\
    -e 's|deb.debian.org/debian|archive.debian.org/debian|g' \\
    -e 's|security.debian.org/debian-security|archive.debian.org/debian-security|g' \\
    /etc/apt/sources.list \\
    && printf 'Acquire::Check-Valid-Until \"false\";\\n' \\
       > /etc/apt/apt.conf.d/99archive
"""
_JAX_FIND_LINKS = (
    "--find-links https://storage.googleapis.com/jax-releases/jax_releases.html"
)
_APT_NETWORK_SETUP = """RUN find /etc/apt -type f \\
    \\( -name '*.list' -o -name '*.sources' \\) \\
    -exec sed -i 's|http://deb.debian.org|https://deb.debian.org|g' {} + \\
    && printf 'Acquire::Retries "3";\\nAcquire::https::Timeout "30";\\n' \\
       > /etc/apt/apt.conf.d/80polybench-network
"""
_PIP_NETWORK_SETUP = """ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=5
"""
_PYAV_BUILD_DEPS = """RUN find /etc/apt -type f \\
    \\( -name '*.list' -o -name '*.sources' \\) \\
    -exec sed -i 's|http://deb.debian.org|https://deb.debian.org|g' {} + \\
    && apt-get \\
       -o Acquire::Retries=3 \\
       -o Acquire::https::Timeout=30 \\
       update \\
    && apt-get \\
       -o Acquire::Retries=3 \\
       -o Acquire::https::Timeout=30 \\
       install -y \\
    pkg-config \\
    libavformat-dev \\
    libavcodec-dev \\
    libavdevice-dev \\
    libavutil-dev \\
    libavfilter-dev \\
    libswscale-dev \\
    libswresample-dev \\
    && rm -rf /var/lib/apt/lists/*

RUN printf 'Cython<3\\n' > /tmp/polybench-build-constraints.txt
ENV PIP_CONSTRAINT=/tmp/polybench-build-constraints.txt
"""


def _import_official_builders() -> tuple[type[Any], type[Any]]:
    try:
        from poly_bench_evaluation.docker_utils import DockerManager
        from poly_bench_evaluation.repo_utils import RepoManager
    except ImportError as exc:
        raise FatalError(
            "PolyBench evaluator modules are unavailable. Install the official "
            "SWE-PolyBench package from a persistent checkout; do not editable-install "
            "it from /tmp."
        ) from exc
    return DockerManager, RepoManager


def local_polybench_image_name(instance_info: dict[str, Any]) -> str:
    instance_id = str(instance_info.get("instance_id", "")).lower()
    language = str(instance_info.get("language", "python")).lower()
    return f"polybench_{language}_{instance_id}"


def _dockerfile_variants(dockerfile: str) -> list[tuple[str, str]]:
    """Return the official Dockerfile followed by narrowly scoped fixes."""
    variants = [("official", dockerfile)]
    candidates: list[tuple[str, str]] = [("", dockerfile)]
    if any(image in dockerfile for image in _BUSTER_IMAGES):
        first_apt = "RUN apt-get update"
        if first_apt in dockerfile:
            fixed = dockerfile.replace(
                first_apt,
                f"{_BUSTER_ARCHIVE_SETUP}\n\n{first_apt}",
                1,
            )
            variants.append(("debian-buster-archive", fixed))
            candidates.append(("debian-buster-archive", fixed))

    for image in _FLOATING_PYTHON_310_IMAGES:
        from_line = f"FROM {image}\n"
        if from_line in dockerfile:
            fixed = dockerfile.replace(
                from_line,
                f"FROM {image}-bullseye\n",
                1,
            )
            variants.append(("python310-bullseye", fixed))
            candidates.append(("python310-bullseye", fixed))
            break

    for candidate_name, candidate in candidates:
        if ".[dev,testing]" not in candidate and ".[testing,flax]" not in candidate:
            continue
        fixed = candidate.replace(
            "pip install --no-cache-dir -e ",
            f"pip install --no-cache-dir {_JAX_FIND_LINKS} -e ",
        )
        if fixed != candidate:
            prefix = f"{candidate_name}+" if candidate_name else ""
            first_apt = "RUN apt-get update"
            if first_apt in fixed:
                fixed = fixed.replace(
                    first_apt,
                    f"{_APT_NETWORK_SETUP}\n\n{first_apt}",
                    1,
                )
                prefix = f"{prefix}apt-network+"
            variants.append((f"{prefix}jax-wheel-archive", fixed))
            if ".[dev,testing]" in fixed:
                install_command = (
                    f"RUN pip install --no-cache-dir {_JAX_FIND_LINKS} -e "
                )
                pyav_fixed = fixed.replace(
                    install_command,
                    f"{_PYAV_BUILD_DEPS}\n\n{install_command}",
                    1,
                )
                variants.append(
                    (
                        f"{prefix}jax-wheel-archive+pyav-build-compat",
                        pyav_fixed,
                    )
                )
    hardened: list[tuple[str, str]] = []
    seen_content = {content for _, content in variants}
    for variant_name, candidate in tuple(variants):
        fixed = candidate
        additions: list[str] = []
        first_apt = "RUN apt-get update"
        if first_apt in fixed and "80polybench-network" not in fixed:
            fixed = fixed.replace(
                first_apt,
                f"{_APT_NETWORK_SETUP}\n\n{first_apt}",
                1,
            )
            additions.append("apt-retry")
        if "pip install" in fixed and "PIP_DEFAULT_TIMEOUT" not in fixed:
            first_newline = fixed.find("\n")
            if first_newline >= 0:
                fixed = (
                    fixed[: first_newline + 1]
                    + _PIP_NETWORK_SETUP
                    + "\n"
                    + fixed[first_newline + 1 :]
                )
                additions.append("pip-retry")
        if additions and fixed not in seen_content:
            suffix = "+".join(additions)
            hardened.append((f"{variant_name}+{suffix}", fixed))
            seen_content.add(fixed)
    variants.extend(hardened)
    return [
        variants[0],
        *sorted(
            variants[1:],
            key=lambda item: item[0].count("+"),
            reverse=True,
        ),
    ]


def _docker_build_with_full_logs(
    docker_manager: Any,
    *,
    repo_path: Path,
    dockerfile_content: str,
    build_timeout: int,
) -> bool:
    """Build with official options, bounded wall time, and retained output."""
    (repo_path / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")
    dockerignore = repo_path / ".dockerignore"
    if dockerignore.exists():
        dockerignore.unlink()

    try:
        result = run_docker_cli(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--tag",
                docker_manager.image_id,
                "--rm",
                str(repo_path),
            ],
            timeout=build_timeout,
        )
        if result.stdout:
            docker_manager.build_logs.extend(result.stdout.rstrip().splitlines())
        if result.stderr:
            docker_manager.build_logs.extend(result.stderr.rstrip().splitlines())
        if result.returncode == 0:
            return True
        docker_manager.build_logs.append(
            f"Build Error: docker build exited with code {result.returncode}"
        )
        return False
    except subprocess.TimeoutExpired as exc:
        for output in (exc.stdout, exc.stderr):
            if output:
                text = output.decode(errors="replace") if isinstance(output, bytes) else output
                docker_manager.build_logs.extend(text.rstrip().splitlines())
        docker_manager.build_logs.append(
            f"Build Timeout: exceeded {build_timeout}s"
        )
        return False
    except Exception as exc:
        docker_manager.build_logs.append(f"Unexpected Error: {exc}")
        return False


def build_polybench_image_from_official_dockerfile(
    instance_info: dict[str, Any],
    *,
    build_timeout: int = 3600,
) -> str:
    """Build an instance image with PolyBench's official DockerManager."""
    instance_id = str(instance_info.get("instance_id", ""))
    repo = str(instance_info.get("repo", ""))
    base_commit = str(instance_info.get("base_commit", ""))
    dockerfile = str(instance_info.get("dockerfile", ""))
    missing = [
        name
        for name, value in (
            ("instance_id", instance_id),
            ("repo", repo),
            ("base_commit", base_commit),
            ("dockerfile", dockerfile),
        )
        if not value
    ]
    if missing:
        raise FatalError(
            "Cannot build PolyBench image; instance metadata missing: "
            + ", ".join(missing)
        )

    DockerManager, RepoManager = _import_official_builders()
    image_id = local_polybench_image_name(instance_info)
    client = create_docker_client(timeout=720)
    docker_manager = DockerManager(image_id=image_id, delete_image=False, client=client)
    try:
        if docker_manager.check_image_local(local_image_name=image_id):
            return image_id

        with tempfile.TemporaryDirectory(prefix="polybench_repo_") as tmp_dir:
            repo_manager = RepoManager(repo_name=repo, repo_path=tmp_dir)
            try:
                repo_manager.clone_repo()
                repo_manager.checkout_commit(commit_hash=base_commit)
                repo_dir = Path(repo_manager.tmp_repo_dir)
                build_success = False
                for variant_name, candidate in _dockerfile_variants(dockerfile):
                    logger.info(
                        "[%s] PolyBench Dockerfile build variant: %s",
                        instance_id,
                        variant_name,
                    )
                    build_success = _docker_build_with_full_logs(
                        docker_manager,
                        repo_path=repo_dir,
                        dockerfile_content=candidate,
                        build_timeout=build_timeout,
                    )
                    if build_success:
                        break
                if not build_success:
                    build_log = "\n".join(docker_manager.build_logs)[-12000:]
                    raise FatalError(
                        f"Official PolyBench Dockerfile build failed for {instance_id} "
                        f"after {len(_dockerfile_variants(dockerfile))} variant(s). "
                        f"Build log tail:\n{build_log}"
                    )
            finally:
                cleanup = getattr(repo_manager, "__del__", None)
                if cleanup is not None:
                    cleanup()
    finally:
        close_docker_client(client)

    logger.info("[%s] Built local PolyBench image: %s", instance_id, image_id)
    return image_id
