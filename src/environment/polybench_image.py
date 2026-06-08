"""PolyBench image acquisition using the official evaluation package."""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile
from typing import Any

import docker
from docker.errors import BuildError

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
    return [
        variants[0],
        *sorted(
            variants[1:],
            key=lambda item: item[0].count("+"),
            reverse=True,
        ),
    ]


def _append_build_event(logs: list[str], event: dict[str, Any]) -> None:
    if stream := event.get("stream"):
        logs.append(str(stream).rstrip())
    if error := event.get("error"):
        logs.append(f"Error: {error}")
    if detail := event.get("errorDetail"):
        logs.append(f"Error Detail: {detail.get('message', detail)}")


def _docker_build_with_full_logs(
    docker_manager: Any,
    *,
    repo_path: Path,
    dockerfile_content: str,
) -> bool:
    """Build with the official options while retaining BuildError output."""
    (repo_path / "Dockerfile").write_text(dockerfile_content)
    dockerignore = repo_path / ".dockerignore"
    if dockerignore.exists():
        dockerignore.unlink()

    try:
        _, build_logs = docker_manager.client.images.build(
            path=str(repo_path),
            tag=docker_manager.image_id,
            rm=True,
            platform="linux/amd64",
        )
        for event in build_logs:
            _append_build_event(docker_manager.build_logs, event)
        return True
    except BuildError as exc:
        for event in exc.build_log:
            _append_build_event(docker_manager.build_logs, event)
        docker_manager.build_logs.append(f"Build Error: {exc}")
        return False
    except Exception as exc:
        docker_manager.build_logs.append(f"Unexpected Error: {exc}")
        return False


def build_polybench_image_from_official_dockerfile(
    instance_info: dict[str, Any],
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
    client = docker.from_env(timeout=720)
    docker_manager = DockerManager(image_id=image_id, delete_image=False, client=client)
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

    logger.info("[%s] Built local PolyBench image: %s", instance_id, image_id)
    return image_id
