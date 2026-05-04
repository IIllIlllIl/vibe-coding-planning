"""Tests for src/evaluator/swe_evaluator.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.evaluator import swe_evaluator
from src.exceptions import FatalError


@pytest.fixture
def instance_info():
    return {
        "instance_id": "astropy__astropy-14539",
        "repo": "astropy/astropy",
        "base_commit": "abc123",
        "image_name": "swebench/astropy-astropy:latest",
    }


class TestEvaluateSuccess:
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_returns_structured_result(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        mock_run_instance.return_value = {"completed": True, "resolved": True}

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert "resolved" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "log_dir" in result
        assert result["resolved"] is True
        assert "logs/run_evaluation" in result["log_dir"]

    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_uses_instance_id_in_prediction(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        calls = []

        def capture_run(test_spec, pred, **kwargs):
            calls.append(pred)
            return {"completed": True, "resolved": False}

        mock_run_instance.side_effect = capture_run

        swe_evaluator.evaluate("diff content", instance_info)

        assert len(calls) == 1
        assert calls[0]["instance_id"] == "astropy__astropy-14539"
        assert calls[0]["model_patch"] == "diff content"
        assert calls[0]["model_name_or_path"] == "plan-code-test"


class TestGetImageName:
    def test_uses_image_name_field(self):
        info = {"image_name": "custom/image:latest"}
        name = swe_evaluator.get_image_name(info)
        assert name == "custom/image:latest"

    def test_derives_from_repo(self):
        info = {"repo": "pandas-dev/pandas"}
        name = swe_evaluator.get_image_name(info)
        assert name == "swebench/pandas-dev-pandas"

    def test_prefers_image_name_over_repo(self):
        info = {"image_name": "custom:latest", "repo": "pandas-dev/pandas"}
        name = swe_evaluator.get_image_name(info)
        assert name == "custom:latest"

    def test_raises_when_no_image_info(self):
        info = {"instance_id": "test-1"}
        with pytest.raises(FatalError, match="Cannot determine Docker image"):
            swe_evaluator.get_image_name(info)

    def test_raises_when_empty_repo(self):
        info = {"repo": ""}
        with pytest.raises(FatalError, match="Cannot determine Docker image"):
            swe_evaluator.get_image_name(info)


class TestMissingInstanceId:
    def test_raises_when_instance_id_missing(self):
        with pytest.raises(FatalError, match="missing 'instance_id'"):
            swe_evaluator.evaluate("diff", {"repo": "test/repo"})


class TestEvaluateFailure:
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_returns_failure_result_on_exception(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        mock_run_instance.side_effect = RuntimeError("Docker not available")

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert result["resolved"] is False
        assert "Docker not available" in result["stderr"]


class TestEvaluateTimeout:
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_default_timeout_is_300(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        captured = {}

        def capture(test_spec, pred, rm_image, force_rebuild, client, run_id, timeout, rewrite_reports):
            captured["timeout"] = timeout
            return {"completed": True, "resolved": False}

        mock_run_instance.side_effect = capture

        swe_evaluator.evaluate("diff", instance_info)
        assert captured["timeout"] == 300

    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_custom_timeout_is_propagated(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        captured = {}

        def capture(test_spec, pred, rm_image, force_rebuild, client, run_id, timeout, rewrite_reports):
            captured["timeout"] = timeout
            return {"completed": True, "resolved": True}

        mock_run_instance.side_effect = capture

        swe_evaluator.evaluate("diff", instance_info, timeout=1800)
        assert captured["timeout"] == 1800


class TestDeriveImageName:
    def test_derive_image_name_aliases_match(self):
        # Backward-compat aliases must point to the same function
        assert swe_evaluator.derive_image_name is swe_evaluator._get_image_name
        assert swe_evaluator.derive_image_name is swe_evaluator.get_image_name

    def test_derive_image_name_replaces_slash(self):
        info = {"repo": "pandas-dev/pandas"}
        result = swe_evaluator.derive_image_name(info)
        # Slashes in repo path must be replaced with hyphens so the
        # resulting image name is a valid Docker reference
        assert "/" not in result.split("/", 1)[1]
        assert result == "swebench/pandas-dev-pandas"


class TestMissingDependency:
    def test_missing_import_raises_fatal_error(self, instance_info):
        with patch(
            "swebench.harness.run_evaluation.run_instance",
            side_effect=ImportError("No module named 'swebench'"),
        ):
            with patch(
                "swebench.harness.test_spec.test_spec.make_test_spec",
            ):
                with patch("docker.from_env"):
                    with pytest.raises(FatalError, match="swebench"):
                        swe_evaluator.evaluate("diff", instance_info)
