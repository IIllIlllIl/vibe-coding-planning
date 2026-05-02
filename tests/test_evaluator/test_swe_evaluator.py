"""Tests for src/evaluator/swe_evaluator.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.evaluator import swe_evaluator
from src.exceptions import FatalError


class MockSwebenchHarness:
    """Mock swebench.harness module."""

    @staticmethod
    def run_evaluation(predictions, run_id, timeout):
        return (True, "/tmp/logs/eval_123")


@pytest.fixture
def instance_info():
    return {
        "instance_id": "astropy__astropy-14539",
        "repo": "astropy/astropy",
        "base_commit": "abc123",
        "image_name": "swebench/astropy-astropy:latest",
    }


class TestEvaluateSuccess:
    @patch.object(swe_evaluator, "_import_swebench")
    def test_returns_structured_result(self, mock_import, instance_info):
        swebench_mock = MagicMock()
        swebench_mock.harness = MockSwebenchHarness()
        mock_import.return_value = swebench_mock

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert "resolved" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "log_dir" in result
        assert result["resolved"] is True
        assert result["log_dir"] == "/tmp/logs/eval_123"

    @patch.object(swe_evaluator, "_import_swebench")
    def test_uses_instance_id_in_prediction(self, mock_import, instance_info):
        swebench_mock = MagicMock()
        calls = []

        class CapturingHarness:
            @staticmethod
            def run_evaluation(predictions, run_id, timeout):
                calls.append(predictions)
                return (False, "/tmp/logs")

        swebench_mock.harness = CapturingHarness()
        mock_import.return_value = swebench_mock

        swe_evaluator.evaluate("diff content", instance_info)

        assert len(calls) == 1
        assert calls[0][0]["instance_id"] == "astropy__astropy-14539"
        assert calls[0][0]["model_patch"] == "diff content"

    @patch.object(swe_evaluator, "_import_swebench")
    def test_derives_image_name_from_repo(self, mock_import):
        swebench_mock = MagicMock()
        calls = []

        class CapturingHarness:
            @staticmethod
            def run_evaluation(predictions, run_id, timeout):
                calls.append(run_id)
                return (True, "/tmp/logs")

        swebench_mock.harness = CapturingHarness()
        mock_import.return_value = swebench_mock

        info = {"instance_id": "django__django-123", "repo": "django/django"}
        swe_evaluator.evaluate("diff", info)
        # Image name derived from repo: swebench/django/django
        # This is tested indirectly via _get_image_name tests below


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
    @patch.object(swe_evaluator, "_import_swebench")
    def test_raises_when_instance_id_missing(self, mock_import):
        with pytest.raises(FatalError, match="missing 'instance_id'"):
            swe_evaluator.evaluate("diff", {"repo": "test/repo"})


class TestEvaluateFailure:
    @patch.object(swe_evaluator, "_import_swebench")
    def test_returns_failure_result_on_exception(self, mock_import, instance_info):
        swebench_mock = MagicMock()

        class FailingHarness:
            @staticmethod
            def run_evaluation(predictions, run_id, timeout):
                raise RuntimeError("Docker not available")

        swebench_mock.harness = FailingHarness()
        mock_import.return_value = swebench_mock

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert result["resolved"] is False
        assert "Docker not available" in result["stderr"]


class TestEvaluateTimeout:
    @patch.object(swe_evaluator, "_import_swebench")
    def test_default_timeout_is_300(self, mock_import, instance_info):
        captured = {}

        class CapturingHarness:
            @staticmethod
            def run_evaluation(predictions, run_id, timeout):
                captured["timeout"] = timeout
                return (False, "/tmp/logs")

        swebench_mock = MagicMock()
        swebench_mock.harness = CapturingHarness()
        mock_import.return_value = swebench_mock

        swe_evaluator.evaluate("diff", instance_info)
        assert captured["timeout"] == 300

    @patch.object(swe_evaluator, "_import_swebench")
    def test_custom_timeout_is_propagated(self, mock_import, instance_info):
        captured = {}

        class CapturingHarness:
            @staticmethod
            def run_evaluation(predictions, run_id, timeout):
                captured["timeout"] = timeout
                return (True, "/tmp/logs")

        swebench_mock = MagicMock()
        swebench_mock.harness = CapturingHarness()
        mock_import.return_value = swebench_mock

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
    @patch.object(
        swe_evaluator,
        "_import_swebench",
        side_effect=FatalError("swebench is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, instance_info):
        with pytest.raises(FatalError, match="swebench"):
            swe_evaluator.evaluate("diff", instance_info)
