"""Tests for the official PolyBench Dockerfile build fallback."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.environment.polybench_image import (
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
):
    docker_manager = MagicMock()
    docker_manager.check_image_local.return_value = False
    docker_manager.docker_build.return_value = 0
    DockerManager = MagicMock(return_value=docker_manager)

    repo_manager = MagicMock()
    repo_manager.tmp_repo_dir = Path("/tmp/repo")
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
    docker_manager.docker_build.assert_called_once_with(
        repo_path=Path("/tmp/repo"),
        dockerfile_content="FROM python:3.10",
    )
