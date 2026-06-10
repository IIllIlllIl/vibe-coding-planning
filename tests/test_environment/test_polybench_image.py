"""Tests for the official PolyBench Dockerfile build fallback."""

import subprocess
from unittest.mock import MagicMock, patch

from src.environment.polybench_image import (
    _docker_build_with_full_logs,
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


@patch("src.environment.polybench_image.run_docker_cli")
@patch("src.environment.polybench_image.close_docker_client")
@patch("src.environment.polybench_image.create_docker_client")
@patch("src.environment.polybench_image._import_official_builders")
def test_builds_with_official_repo_and_docker_managers(
    mock_import,
    mock_create_client,
    mock_close_client,
    mock_run,
    tmp_path,
):
    docker_manager = MagicMock()
    docker_manager.image_id = "polybench_python_org__repo-1"
    docker_manager.check_image_local.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="built\n", stderr="")
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
    mock_run.assert_called_once_with(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--tag",
            "polybench_python_org__repo-1",
            "--rm",
            str(tmp_path),
        ],
        timeout=3600,
    )
    mock_close_client.assert_called_once_with(mock_create_client.return_value)


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
        "RUN apt-get update && apt-get install -y git\n"
        'RUN pip install --no-cache-dir -e ".[dev,testing]"\n'
    )

    variants = _dockerfile_variants(dockerfile)

    assert [name for name, _ in variants] == [
        "official",
        "apt-network+jax-wheel-archive+pyav-build-compat",
        "apt-network+jax-wheel-archive",
    ]
    assert (
        "--find-links https://storage.googleapis.com/jax-releases/"
        "jax_releases.html -e"
    ) in variants[2][1]
    assert "libavformat-dev" in variants[1][1]
    assert "pkg-config" in variants[1][1]
    assert "Cython<3" in variants[1][1]
    assert "PIP_CONSTRAINT" in variants[1][1]
    assert "https://deb.debian.org" in variants[1][1]
    assert 'Acquire::Retries "3"' in variants[1][1]


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


@patch("src.environment.polybench_image.run_docker_cli")
def test_docker_build_timeout_is_recorded(mock_run, tmp_path):
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["docker", "build"],
        timeout=15,
        output="partial stdout\n",
        stderr="partial stderr\n",
    )
    docker_manager = MagicMock()
    docker_manager.image_id = "polybench_python_org__repo-1"
    docker_manager.build_logs = []

    success = _docker_build_with_full_logs(
        docker_manager,
        repo_path=tmp_path,
        dockerfile_content="FROM python:3.10\n",
        build_timeout=15,
    )

    assert success is False
    assert docker_manager.build_logs == [
        "partial stdout",
        "partial stderr",
        "Build Timeout: exceeded 15s",
    ]
