"""Tests for the official PolyBench Dockerfile build fallback."""

from unittest.mock import MagicMock, patch

from src.environment.polybench_image import (
    _dockerfile_variants,
    build_polybench_image_from_official_dockerfile,
    local_polybench_image_name,
)


def test_local_image_name_matches_official_evaluator():
    assert (
        local_polybench_image_name(
            {"instance_id": "Org__Repo-1", "language": "Python"}
        )
        == "polybench_python_org__repo-1"
    )


@patch("src.environment.polybench_image.docker.from_env")
@patch("src.environment.polybench_image._import_official_builders")
def test_builds_with_official_repo_and_docker_managers(
    mock_import,
    mock_docker_from_env,
    tmp_path,
):
    docker_manager = MagicMock()
    docker_manager.image_id = "polybench_python_org__repo-1"
    docker_manager.check_image_local.return_value = False
    docker_manager.client.images.build.return_value = (MagicMock(), [])
    DockerManager = MagicMock(return_value=docker_manager)

    repo_manager = MagicMock()
    repo_manager.tmp_repo_dir = tmp_path
    RepoManager = MagicMock(return_value=repo_manager)
    mock_import.return_value = (DockerManager, RepoManager)

    image = build_polybench_image_from_official_dockerfile(
        {
            "instance_id": "org__repo-1",
            "repo": "org/repo",
            "base_commit": "abc",
            "dockerfile": "FROM python:3.10",
            "language": "Python",
        }
    )

    assert image == "polybench_python_org__repo-1"
    repo_manager.clone_repo.assert_called_once()
    repo_manager.checkout_commit.assert_called_once_with(commit_hash="abc")
    docker_manager.client.images.build.assert_called_once_with(
        path=str(tmp_path),
        tag="polybench_python_org__repo-1",
        rm=True,
        platform="linux/amd64",
    )


def test_buster_dockerfile_gets_archive_fallback():
    dockerfile = """FROM public.ecr.aws/docker/library/python:3.8.16-slim-buster

RUN apt-get update && apt-get install -y git
"""

    variants = _dockerfile_variants(dockerfile)

    assert [name for name, _ in variants] == [
        "official",
        "debian-buster-archive",
    ]
    assert "archive.debian.org/debian" in variants[1][1]
    assert "Acquire::Check-Valid-Until" in variants[1][1]


def test_current_debian_dockerfile_has_no_compatibility_variant():
    assert _dockerfile_variants(
        "FROM python:3.10-slim-bookworm\nRUN apt-get update\n"
    ) == [
        ("official", "FROM python:3.10-slim-bookworm\nRUN apt-get update\n")
    ]


def test_transformers_dev_extras_get_jax_archive_fallback():
    dockerfile = (
        "FROM python:3.10-slim-bookworm\n"
        'RUN pip install --no-cache-dir -e ".[dev,testing]"\n'
    )

    variants = _dockerfile_variants(dockerfile)

    assert [name for name, _ in variants] == [
        "official",
        "jax-wheel-archive+pyav-build-compat",
        "jax-wheel-archive",
    ]
    assert (
        "--find-links https://storage.googleapis.com/jax-releases/"
        "jax_releases.html -e"
    ) in variants[2][1]
    assert "libavformat-dev" in variants[1][1]
    assert "pkg-config" in variants[1][1]
    assert "Cython<3" in variants[1][1]
    assert "PIP_CONSTRAINT" in variants[1][1]


def test_python310_slim_gets_bullseye_fallback():
    dockerfile = "FROM public.ecr.aws/docker/library/python:3.10-slim\n"

    variants = _dockerfile_variants(dockerfile)

    assert variants == [
        ("official", dockerfile),
        (
            "python310-bullseye",
            "FROM public.ecr.aws/docker/library/python:3.10-slim-bullseye\n",
        ),
    ]
