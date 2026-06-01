"""Tests for src/evaluator/polybench_evaluator.py."""

from unittest.mock import patch, MagicMock

import pytest

from src.evaluator.polybench_evaluator import evaluate_polybench_instance
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
            self.create_container = MagicMock()
            self.apply_patch_to_container = MagicMock(side_effect=kwargs.get("apply_patch_side_effect", [0, 0]))
            self.docker_run = MagicMock()
        def __del__(self):
            pass
    return _DM()


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
        pb["DockerManager"] = lambda **kwargs: _make_docker_manager()

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

    @patch("docker.from_env")
    @patch("src.evaluator.polybench_evaluator._import_polybench")
    def test_returns_failure_when_image_unavailable(
        self,
        mock_import_polybench,
        mock_docker_from_env,
        polybench_instance_info,
    ):
        """When the Docker image is not available locally or in GHCR, fail fast."""
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

        result = evaluate_polybench_instance(
            patch="some patch",
            instance_info=polybench_instance_info,
        )

        assert result["resolved"] is False
        assert "unavailable" in result["error_info"].lower()

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
