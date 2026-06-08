"""Tests for src/evaluator/polybench_evaluator.py."""

from unittest.mock import patch, MagicMock

import pytest

from src.evaluator.polybench_evaluator import (
    _reset_container_worktree,
    _try_pull_prebuilt_image_with_fallback,
    evaluate_polybench_instance,
)
from src.exceptions import FatalError


@pytest.fixture
def polybench_instance_info():
    return {
        "instance_id": "test__repo-1234",
        "repo": "test/repo",
        "base_commit": "abc123",
        "patch": "diff content",
        "test_patch": "test diff",
        "problem_statement": "Fix the bug",
        "language": "Python",
        "dockerfile": "FROM python:3.10",
        "f2p": ["test_f2p"],
        "p2p": ["test_p2p"],
        "f2f": [],
        "test_command": "pytest tests/",
        "modified_nodes": [],
        "model_patch": "",
        "dataset_type": "polybench",
    }


def _make_pb_imports():
    """Return a mock dict matching the shape _import_polybench returns."""
    return {
        "DockerManager": MagicMock(),
        "PolyBenchInstance": MagicMock(),
        "instance_level_scoring": MagicMock(),
        "store_instance_level_output": MagicMock(),
        "DEFAULT_TIMEOUT": 1800,
        "REPO_TO_PARSER_CLASS": {"test/repo": "PythonPyUnit"},
    }


def _make_docker_manager(**kwargs):
    """Return a plain object that quacks like DockerManager."""
    class _DM:
        def __init__(self):
            self.check_image_local = MagicMock(return_value=kwargs.get("check_image_local", True))
            self.try_pull_prebuilt_image = MagicMock(return_value=kwargs.get("try_pull_prebuilt_image", False))
            self.run_logs = kwargs.get("run_logs", ["test output", "exit code: 0"])
            self.container = MagicMock()
            self.container.exec_run.return_value = MagicMock(exit_code=0, output=b"")
            self._get_workdir_from_image = MagicMock(return_value="/testbed")
            self.create_container = MagicMock()
            self.apply_patch_to_container = MagicMock(side_effect=kwargs.get("apply_patch_side_effect", [0, 0]))
            self.docker_run = MagicMock()
        def __del__(self):
            pass
    return _DM()


def test_try_pull_prebuilt_image_with_fallback_uses_older_tag():
    docker_manager = MagicMock()
    docker_manager.try_pull_prebuilt_image.side_effect = [False, True]

    result = _try_pull_prebuilt_image_with_fallback(
        docker_manager,
        "test__repo-1234",
        tags=("v1.1", "v1.0", "latest"),
    )

    assert result is True
    assert docker_manager.try_pull_prebuilt_image.call_args_list == [
        (("test__repo-1234",), {"version": "v1.1"}),
        (("test__repo-1234",), {"version": "v1.0"}),
    ]


def test_try_pull_prebuilt_image_with_fallback_returns_false_after_all_tags():
    docker_manager = MagicMock()
    docker_manager.try_pull_prebuilt_image.return_value = False

    result = _try_pull_prebuilt_image_with_fallback(
        docker_manager,
        "test__repo-1234",
        tags=("v1.1", "v1.0", "latest"),
    )

    assert result is False
    assert docker_manager.try_pull_prebuilt_image.call_count == 3


def test_reset_container_worktree_cleans_dirty_official_image():
    docker_manager = _make_docker_manager()

    _reset_container_worktree(docker_manager, "test__repo-1234")

    docker_manager.container.exec_run.assert_called_once_with(
        cmd=["sh", "-lc", "git reset --hard HEAD && git clean -fd"],
        workdir="/testbed",
        user="root",
    )


class TestEvaluatePolybenchInstance:
    @patch("docker.from_env")
    @patch("src.evaluator.polybench_evaluator._import_polybench")
    def test_runs_full_evaluation_flow(
        self,
        mock_import_polybench,
        mock_docker_from_env,
        polybench_instance_info,
    ):
        """The evaluator creates a container, applies patches, runs tests, and scores."""
        pb = _make_pb_imports()
        captured_dm_kwargs = {}

        def make_manager(**kwargs):
            captured_dm_kwargs.update(kwargs)
            return _make_docker_manager()

        pb["DockerManager"] = make_manager

        mock_output = MagicMock()
        mock_output.resolved = True
        pb["instance_level_scoring"] = lambda **kwargs: mock_output
        pb["store_instance_level_output"] = lambda **kwargs: None

        mock_inst = MagicMock()
        mock_inst.repo = "test/repo"
        mock_inst.test_patch = "test diff"
        mock_inst.test_command = "pytest tests/"
        mock_inst.f2p = ["test_f2p"]
        mock_inst.p2p = ["test_p2p"]
        mock_inst.language = "Python"
        pb["PolyBenchInstance"] = lambda **kwargs: mock_inst

        mock_import_polybench.return_value = pb

        # Mock parser
        mock_parser = MagicMock()
        mock_parser.parse.return_value = {"passed": 1, "failed": 0}

        with patch("importlib.import_module") as mock_import:
            mock_parsers_mod = MagicMock()
            mock_parsers_mod.PythonPyUnit = lambda **kwargs: mock_parser
            mock_import.return_value = mock_parsers_mod

            result = evaluate_polybench_instance(
                patch="model patch content",
                instance_info=polybench_instance_info,
            )

        assert result["resolved"] is True
        assert result["error_info"] is None
        assert captured_dm_kwargs["delete_image"] is False

    @patch("docker.from_env")
    @patch("src.evaluator.polybench_evaluator._import_polybench")
    def test_returns_failure_when_patch_apply_fails(
        self,
        mock_import_polybench,
        mock_docker_from_env,
        polybench_instance_info,
    ):
        """When the code patch cannot be applied, resolved=False."""
        pb = _make_pb_imports()
        pb["DockerManager"] = lambda **kwargs: _make_docker_manager(
            apply_patch_side_effect=[0, Exception("patch failed")]
        )

        mock_output = MagicMock()
        mock_output.resolved = False
        pb["instance_level_scoring"] = lambda **kwargs: mock_output
        pb["store_instance_level_output"] = lambda **kwargs: None

        mock_inst = MagicMock()
        mock_inst.repo = "test/repo"
        mock_inst.test_patch = "test diff"
        mock_inst.f2p = ["test_f2p"]
        mock_inst.p2p = ["test_p2p"]
        mock_inst.language = "Python"
        pb["PolyBenchInstance"] = lambda **kwargs: mock_inst

        mock_import_polybench.return_value = pb

        result = evaluate_polybench_instance(
            patch="bad patch",
            instance_info=polybench_instance_info,
        )

        assert result["resolved"] is False
        assert "patch apply failed" in result["stderr"].lower()

    @patch(
        "src.environment.polybench_image.build_polybench_image_from_official_dockerfile",
        return_value="polybench_python_test__repo-1234",
    )
    @patch("docker.from_env")
    @patch("src.evaluator.polybench_evaluator._import_polybench")
    def test_builds_official_dockerfile_when_image_unavailable(
        self,
        mock_import_polybench,
        mock_docker_from_env,
        mock_build_image,
        polybench_instance_info,
    ):
        """GHCR failure falls back to the official instance Dockerfile."""
        pb = _make_pb_imports()
        pb["DockerManager"] = lambda **kwargs: _make_docker_manager(
            check_image_local=False,
            try_pull_prebuilt_image=False,
        )

        mock_output = MagicMock()
        mock_output.resolved = False
        pb["instance_level_scoring"] = lambda **kwargs: mock_output
        pb["store_instance_level_output"] = lambda **kwargs: None

        mock_inst = MagicMock()
        mock_inst.repo = "test/repo"
        mock_inst.f2p = ["test_f2p"]
        mock_inst.p2p = ["test_p2p"]
        mock_inst.language = "Python"
        pb["PolyBenchInstance"] = lambda **kwargs: mock_inst

        mock_import_polybench.return_value = pb

        mock_parser = MagicMock()
        mock_parser.parse.return_value = {}
        with patch("importlib.import_module") as mock_import:
            mock_parsers_mod = MagicMock()
            mock_parsers_mod.PythonPyUnit = lambda **kwargs: mock_parser
            mock_import.return_value = mock_parsers_mod
            result = evaluate_polybench_instance(
                patch="some patch",
                instance_info=polybench_instance_info,
            )

        mock_build_image.assert_called_once_with(polybench_instance_info)
        assert result["resolved"] is False

    def test_raises_when_instance_id_missing(self):
        with pytest.raises(FatalError, match="missing 'instance_id'"):
            evaluate_polybench_instance("patch", {"repo": "test/repo"})

    @patch("docker.from_env")
    @patch("src.evaluator.polybench_evaluator._import_polybench")
    def test_empty_patch_no_generation(
        self,
        mock_import_polybench,
        mock_docker_from_env,
        polybench_instance_info,
    ):
        """Empty patch should be scored as generation=False."""
        pb = _make_pb_imports()
        pb["DockerManager"] = lambda **kwargs: _make_docker_manager()

        mock_output = MagicMock()
        mock_output.resolved = False
        pb["instance_level_scoring"] = lambda **kwargs: mock_output
        pb["store_instance_level_output"] = lambda **kwargs: None

        mock_inst = MagicMock()
        mock_inst.repo = "test/repo"
        mock_inst.test_patch = "test diff"
        mock_inst.f2p = ["test_f2p"]
        mock_inst.p2p = ["test_p2p"]
        mock_inst.language = "Python"
        pb["PolyBenchInstance"] = lambda **kwargs: mock_inst

        mock_import_polybench.return_value = pb

        result = evaluate_polybench_instance(
            patch="",
            instance_info=polybench_instance_info,
        )

        assert result["resolved"] is False
        assert result["error_info"] == "Empty patch"
