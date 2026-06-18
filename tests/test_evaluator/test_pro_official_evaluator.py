"""Tests for centralized Docker capacity in the Pro evaluator."""

from unittest.mock import MagicMock, patch

from src.evaluator.pro_official_evaluator import evaluate_pro_instance


@patch("src.evaluator.pro_official_evaluator.ensure_project_image_local")
@patch("src.evaluator.pro_official_evaluator.get_docker_capacity_window")
@patch("src.evaluator.pro_official_evaluator._ensure_official_deps")
def test_pro_evaluator_runs_inside_shared_docker_lease(
    mock_ensure_deps, mock_get_window, mock_ensure_image, tmp_path
):
    lease = MagicMock()
    mock_get_window.return_value.lease.return_value = lease
    mock_ensure_deps.return_value.return_value = {"tests": []}
    instance = {
        "instance_id": "org__repo-1",
        "dockerhub_tag": "image:latest",
        "before_repo_set_cmd": "",
        "selected_test_files_to_run": "",
        "fail_to_pass": "[]",
        "pass_to_pass": "[]",
        "base_commit": "abc",
        "repo": "org/repo",
    }

    result = evaluate_pro_instance(
        patch="diff",
        instance_info=instance,
        output_dir=str(tmp_path),
    )

    assert result["resolved"] is True
    lease.__enter__.assert_called_once()
    lease.__exit__.assert_called_once()
    mock_ensure_image.assert_called_once_with(
        "jefzda/sweap-images:image:latest",
        timeout=300,
    )


@patch("src.evaluator.pro_official_evaluator.ensure_project_image_local")
@patch("src.evaluator.pro_official_evaluator.get_docker_capacity_window")
@patch("src.evaluator.pro_official_evaluator._ensure_official_deps")
def test_pro_evaluator_prefers_explicit_image_name(
    mock_ensure_deps, mock_get_window, mock_ensure_image, tmp_path
):
    mock_get_window.return_value.lease.return_value = MagicMock()
    mock_ensure_deps.return_value.return_value = {"tests": []}
    instance = {
        "instance_id": "org__repo-1",
        "image_name": "jefzda/sweap-images:explicit",
        "dockerhub_tag": "fallback",
        "before_repo_set_cmd": "",
        "selected_test_files_to_run": "",
        "fail_to_pass": "[]",
        "pass_to_pass": "[]",
        "base_commit": "abc",
        "repo": "org/repo",
    }

    evaluate_pro_instance(
        patch="diff",
        instance_info=instance,
        output_dir=str(tmp_path),
    )

    mock_ensure_image.assert_called_once_with(
        "jefzda/sweap-images:explicit",
        timeout=300,
    )
