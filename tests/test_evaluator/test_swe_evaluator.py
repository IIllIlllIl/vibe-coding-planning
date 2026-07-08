"""Tests for src/evaluator/swe_evaluator.py."""

import json as json_mod
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.evaluator.swe_apptainer_evaluator import evaluate_apptainer
from src.evaluator import swe_evaluator
from src.exceptions import FatalError
from src.optimization.config import ContainerConfig


@pytest.fixture
def instance_info():
    return {
        "instance_id": "astropy__astropy-14539",
        "repo": "astropy/astropy",
        "base_commit": "abc123",
        "image_name": "swebench/astropy-astropy:latest",
    }


class TestEvaluateSuccess:
    @patch("src.environment.docker_env.ensure_project_image_local")
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_returns_structured_result(
        self,
        mock_docker,
        mock_make_spec,
        mock_run_instance,
        mock_ensure_image,
        instance_info,
    ):
        mock_run_instance.return_value = {"completed": True, "resolved": True}

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert "resolved" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "log_dir" in result
        assert result["resolved"] is True
        assert "logs/run_evaluation" in result["log_dir"]
        mock_ensure_image.assert_called_once_with(
            "swebench/astropy-astropy:latest",
            timeout=300,
        )

    @patch("src.environment.docker_env.ensure_project_image_local")
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_prepares_image_before_official_run_instance(
        self, mock_docker, mock_make_spec, mock_run_instance, mock_ensure_image
    ):
        events = []
        info = {"instance_id": "pandas-dev__pandas-1234"}

        def ensure_image(*args, **kwargs):
            events.append("ensure")

        def run_instance(*args, **kwargs):
            events.append("run_instance")
            return {"completed": True, "resolved": True}

        mock_ensure_image.side_effect = ensure_image
        mock_run_instance.side_effect = run_instance

        swe_evaluator.evaluate("diff content", info, timeout=1800)

        assert events == ["ensure", "run_instance"]
        mock_ensure_image.assert_called_once_with(
            "swebench/sweb.eval.x86_64.pandas-dev_1776_pandas-1234:latest",
            timeout=1800,
        )

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

    def test_derives_from_instance_id(self):
        info = {"instance_id": "pandas-dev__pandas-1234"}
        name = swe_evaluator.get_image_name(info)
        assert name == "swebench/sweb.eval.x86_64.pandas-dev_1776_pandas-1234:latest"

    def test_prefers_image_name_over_instance_id(self):
        info = {"image_name": "custom:latest", "instance_id": "pandas-dev__pandas-1234"}
        name = swe_evaluator.get_image_name(info)
        assert name == "custom:latest"

    def test_raises_when_no_image_info(self):
        info = {"repo": "pandas-dev/pandas"}
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
            captured["rm_image"] = rm_image
            return {"completed": True, "resolved": False}

        mock_run_instance.side_effect = capture

        swe_evaluator.evaluate("diff", instance_info)
        assert captured["timeout"] == 300
        assert captured["rm_image"] is False

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

    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_delete_image_can_be_enabled(
        self, mock_docker, mock_make_spec, mock_run_instance, instance_info
    ):
        captured = {}

        def capture(test_spec, pred, rm_image, force_rebuild, client, run_id, timeout, rewrite_reports):
            captured["rm_image"] = rm_image
            return {"completed": True, "resolved": False}

        mock_run_instance.side_effect = capture

        swe_evaluator.evaluate("diff", instance_info, delete_image=True)

        assert captured["rm_image"] is True


class TestDeriveImageName:
    def test_derive_image_name_aliases_match(self):
        # Backward-compat aliases must point to the same function
        assert swe_evaluator.derive_image_name is swe_evaluator._get_image_name
        assert swe_evaluator.derive_image_name is swe_evaluator.get_image_name

    def test_derive_image_name_replaces_double_underscore(self):
        info = {"instance_id": "pandas-dev__pandas-1234"}
        result = swe_evaluator.derive_image_name(info)
        # Double underscores in instance_id are replaced with _1776_ per
        # the SWE-bench remote-image namespace convention.
        assert "__" not in result
        assert "_1776_" in result
        assert result == "swebench/sweb.eval.x86_64.pandas-dev_1776_pandas-1234:latest"


class TestEvaluateCompletedFalse:
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_completed_false_returns_failure(self, mock_docker, mock_make_spec, mock_run_instance, instance_info):
        mock_run_instance.return_value = {"completed": False, "resolved": False}

        result = swe_evaluator.evaluate("diff content", instance_info)

        assert result["resolved"] is False
        assert "completed=False" in result["stderr"]
        assert result["error_info"] is not None


class TestEvaluateReport:
    @patch("swebench.harness.run_evaluation.run_instance")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    @patch("docker.from_env")
    def test_reads_report_json(self, mock_docker, mock_make_spec, mock_run_instance, instance_info):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs" / "run_evaluation" / "eval_astropy__astropy-14539" / "plan-code-test" / "astropy__astropy-14539"
            log_dir.mkdir(parents=True)
            report = {
                "astropy__astropy-14539": {
                    "test_output": "test passed",
                    "error": "",
                }
            }
            (log_dir / "report.json").write_text(json_mod.dumps(report), encoding="utf-8")

            with patch("src.evaluator.swe_evaluator.Path") as mock_path_cls:
                mock_path_cls.return_value.resolve.return_value = log_dir

                mock_run_instance.return_value = {"completed": True, "resolved": True}
                result = swe_evaluator.evaluate("diff", instance_info)

                assert result["resolved"] is True


class TestEvaluateApptainer:
    @patch("src.evaluator.swe_apptainer_evaluator.ApptainerEnvironment")
    @patch("swebench.harness.grading.get_eval_report")
    @patch("swebench.harness.test_spec.test_spec.make_test_spec")
    def test_reuses_official_spec_and_grading(
        self,
        mock_make_spec,
        mock_get_report,
        mock_env_cls,
        tmp_path,
        instance_info,
    ):
        commands = []
        mock_make_spec.return_value = SimpleNamespace(eval_script="echo tests")
        mock_get_report.return_value = {
            "astropy__astropy-14539": {"resolved": True, "error": ""}
        }

        class FakeEnv:
            def execute(self, command, cwd="", *, timeout=None):
                commands.append(command)
                if "git apply --verbose .vibe_patch.diff" in command:
                    return {"returncode": 0, "output": "applied"}
                return {"returncode": 0, "output": "test output"}

            def cleanup(self):
                commands.append("cleanup")

        mock_env_cls.return_value = FakeEnv()

        result = evaluate_apptainer(
            "diff --git a/a.py b/a.py\n",
            instance_info,
            container=ContainerConfig(
                runtime="apptainer",
                sif_cache_dir=tmp_path / "sifs",
            ),
            capacity_window=object(),
            phase_workdir=tmp_path / "eval-workdir",
            timeout=123,
        )

        assert result["resolved"] is True
        assert result["stdout"] == "test output"
        mock_make_spec.assert_called_once_with(instance_info, namespace="swebench")
        mock_get_report.assert_called_once()
        assert any(".vibe_patch.diff" in command for command in commands)
        assert any("/bin/bash .vibe_eval.sh" in command for command in commands)
        assert commands[-1] == "cleanup"

    def test_rejects_non_standard_datasets(self, tmp_path, instance_info):
        info = {**instance_info, "dataset_type": "polybench"}

        with pytest.raises(FatalError, match="standard SWE-bench/Verified"):
            evaluate_apptainer(
                "diff",
                info,
                container=ContainerConfig(
                    runtime="apptainer",
                    sif_cache_dir=tmp_path / "sifs",
                ),
                capacity_window=object(),
                phase_workdir=tmp_path / "eval-workdir",
            )


class TestPolybenchRouting:
    def test_routes_polybench_by_dataset_type(self):
        poly_info = {
            "instance_id": "huggingface__transformers-3147",
            "repo": "huggingface/transformers",
            "base_commit": "abc123",
            "dataset_type": "polybench",
        }

        with patch(
            "src.evaluator.polybench_evaluator.evaluate_polybench_instance"
        ) as mock_eval:
            mock_eval.return_value = {
                "resolved": True,
                "stdout": "tests passed",
                "stderr": "",
                "log_dir": "/tmp/polybench",
                "error_info": None,
                "report": {},
            }
            result = swe_evaluator.evaluate("diff content", poly_info)

        assert result["resolved"] is True
        mock_eval.assert_called_once()
        call_args = mock_eval.call_args
        assert call_args.kwargs["patch"] == "diff content"
        assert call_args.kwargs["instance_info"] == poly_info

    def test_routes_pro_by_dockerhub_tag(self):
        pro_info = {
            "instance_id": "ansible__ansible-1234",
            "repo": "ansible/ansible",
            "dockerhub_tag": "some-tag",
        }

        with patch(
            "src.evaluator.pro_official_evaluator.evaluate_pro_instance"
        ) as mock_eval:
            mock_eval.return_value = {
                "resolved": False,
                "stdout": "",
                "stderr": "",
                "log_dir": "/tmp/pro",
                "error_info": None,
                "report": {},
            }
            result = swe_evaluator.evaluate("diff", pro_info)

        assert result["resolved"] is False
        mock_eval.assert_called_once()

    def test_routes_swebench_by_default(self):
        verified_info = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "base_commit": "abc123",
        }

        with patch("swebench.harness.run_evaluation.run_instance") as mock_run:
            with patch("swebench.harness.test_spec.test_spec.make_test_spec"):
                with patch("docker.from_env"):
                    mock_run.return_value = {"completed": True, "resolved": True}
                    result = swe_evaluator.evaluate("diff", verified_info)

        assert result["resolved"] is True
        mock_run.assert_called_once()


class TestImportSwebench:
    def test_import_swebench_success(self):
        swebench = swe_evaluator._import_swebench()
        assert swebench is not None

    def test_import_swebench_failure(self):
        with patch("builtins.__import__", side_effect=ImportError("No module named 'swebench'")):
            with pytest.raises(FatalError, match="swebench"):
                swe_evaluator._import_swebench()


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
