"""PolyBench image acquisition using the official evaluation package."""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile
from typing import Any

import docker

from src.exceptions import FatalError

logger = logging.getLogger(__name__)


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
            build_success = 1
            for attempt in range(1, 4):
                logger.info(
                    "[%s] Official Dockerfile build attempt %d/3",
                    instance_id,
                    attempt,
                )
                build_success = docker_manager.docker_build(
                    repo_path=repo_dir,
                    dockerfile_content=dockerfile,
                )
                if build_success == 0:
                    break
            if build_success != 0:
                build_log = "\n".join(docker_manager.build_logs)[-4000:]
                raise FatalError(
                    f"Official PolyBench Dockerfile build failed for {instance_id} "
                    f"after 3 attempts. Build log tail:\n{build_log}"
                )
        finally:
            cleanup = getattr(repo_manager, "__del__", None)
            if cleanup is not None:
                cleanup()

    logger.info("[%s] Built local PolyBench image: %s", instance_id, image_id)
    return image_id
