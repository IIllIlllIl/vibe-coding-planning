"""Tests for src.pipeline_check."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import (
    AgentConfig,
    CheckerConfig,
    Config,
    DockerConfig,
    EvaluatorConfig,
    PromptConfig,
    SystemConfig,
)
from src.exceptions import FatalError, TaskError
from src.pipeline_check import (
    _collect_runtime_versions,
    _dataset_short,
    _finalize_writer,
    _run_instance_core,
    run_instance,
)


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            n=1,
            instances=["astropy__astropy-14539"],
            output_dir="./output",
            batch_id="checker-test",
            dataset="SWE-bench/SWE-bench_Verified",
        ),
        prompts=PromptConfig(),
        docker=DockerConfig(
            image_builder_script="./scripts/build.sh",
            workdir="/testbed",
        ),
        agent=AgentConfig(max_steps=10),
        evaluator=EvaluatorConfig(timeout=300),
        checker=CheckerConfig(
            enabled=True,
            rules_path="./rules.json",
            model="deepseek-v4-flash",
        ),
        api_key="test-key",
    )


class TestDatasetShort:
    def test_verified(self):
        assert _dataset_short("SWE-bench/SWE-bench_Verified") == "SWE-bench_Verified"

    def test_pro(self):
        assert _dataset_short("SWE-bench/SWE-bench_Pro") == "SWE-bench_Pro"

    def test_empty(self):
        assert _dataset_short("") == "default"

    def test_no_slash(self):
        assert _dataset_short("my-dataset") == "my-dataset"


class TestCollectRuntimeVersions:
    def test_returns_dict(self):
        versions = _collect_runtime_versions()
        assert isinstance(versions, dict)
        assert "mini_swe_agent" in versions
        assert "swebench" in versions
        assert "litellm" in versions


class TestFinalizeWriter:
    def test_finalize_returns_json(self, config):
        mock_writer = MagicMock()
        import tempfile

        tmp_result = Path(tempfile.gettempdir()) / "test_finalize.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result

        result = _finalize_writer(mock_writer, config, "i1")
        assert result == {"plans": [], "run_id": "test"}


class TestRunInstanceCoreHappyPath:
    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    @patch("src.pipeline_check.plan_agent.run")
    @patch("src.pipeline_check.check_agent.run")
    @patch("src.pipeline_check.code_agent.run")
    @patch("src.pipeline_check.evaluate")
    @patch("src.pipeline_check.save_trajectory")
    @patch("src.pipeline_check.load_aggregated_rules")
    @patch("src.pipeline_check.format_rules_for_prompt")
    def test_full_pipeline_with_checker_enabled(
        self,
        mock_format_rules,
        mock_load_rules,
        mock_save_traj,
        mock_eval,
        mock_code,
        mock_check,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
        tmp_path,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/img:latest",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan content", [{"role": "system", "content": "plan"}])
        mock_check.return_value = (
            {"passed": True, "violations": [], "overall_assessment": "Good"},
            [{"role": "assistant", "content": "check"}],
        )
        mock_code.return_value = ("diff --git\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", [])
        mock_eval.return_value = {"resolved": True, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "trajectory.json"
        mock_load_rules.return_value = {"always": ["r1"], "branches": []}
        mock_format_rules.return_value = "Rule text"

        mock_writer = MagicMock()

        tmp_result = tmp_path / "test_pcheck1.json"
        tmp_result.write_text(
            json.dumps({"plans": [], "run_id": "test"}),
            encoding="utf-8",
        )
        mock_writer.finalize.return_value = tmp_result
        out_dir = tmp_path / "output" / "test"
        out_dir.mkdir(parents=True)
        mock_writer.output_dir = str(out_dir)
        plan_path = out_dir / "plans" / "plan.md"
        plan_path.parent.mkdir(parents=True)
        mock_writer.save_plan.return_value = plan_path

        result = _run_instance_core("astropy__astropy-14539", config, mock_writer)

        mock_docker.start.assert_called_once()
        mock_plan.assert_called_once()
        mock_check.assert_called_once()
        mock_code.assert_called_once()
        mock_eval.assert_called_once()
        mock_docker.stop.assert_called_once()

        assert result["check_result"]["passed"] is True
        assert result["test_results"]["resolved"] is True
        assert result["resolved"] is True

    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    @patch("src.pipeline_check.plan_agent.run")
    @patch("src.pipeline_check.code_agent.run")
    @patch("src.pipeline_check.evaluate")
    @patch("src.pipeline_check.save_trajectory")
    def test_pipeline_with_checker_disabled(
        self,
        mock_save_traj,
        mock_eval,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
        tmp_path,
    ):
        config_disabled = Config(
            system=config.system,
            prompts=config.prompts,
            docker=config.docker,
            agent=config.agent,
            evaluator=config.evaluator,
            checker=CheckerConfig(enabled=False),
            analysis=config.analysis,
            api_key=config.api_key,
        )

        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "r/r",
            "problem_statement": "p",
            "image_name": "img",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_code.return_value = ("diff", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "traj.json"

        mock_writer = MagicMock()

        tmp_result = tmp_path / "test_pcheck2.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        out_dir = tmp_path / "output" / "test"
        out_dir.mkdir(parents=True)
        mock_writer.output_dir = str(out_dir)
        plan_path = out_dir / "plans" / "plan.md"
        plan_path.parent.mkdir(parents=True)
        mock_writer.save_plan.return_value = plan_path

        result = _run_instance_core("i1", config_disabled, mock_writer)

        # check_agent.run should NOT be called when checker is disabled
        assert result["check_result"]["passed"] is True
        assert result["check_result"]["overall_assessment"] == "Checker disabled"


class TestRunInstanceCoreErrorHandling:
    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    def test_instance_load_failed(
        self,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.side_effect = TaskError("Dataset not found")
        mock_loader_cls.return_value = mock_loader

        mock_writer = MagicMock()
        import tempfile

        tmp_result = Path(tempfile.gettempdir()) / "test_pcheck3.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result

        result = _run_instance_core("i1", config, mock_writer)

        mock_writer.record_error.assert_called_once()
        call = mock_writer.record_error.call_args
        assert call.kwargs["error_type"] == "instance_load_failed"
        assert call.kwargs["skipped"] is True

    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    def test_docker_start_failed(
        self,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "r/r",
            "problem_statement": "p",
            "image_name": "img",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker.start.side_effect = Exception("Docker daemon not running")
        mock_docker_cls.return_value = mock_docker

        mock_writer = MagicMock()
        import tempfile

        tmp_result = Path(tempfile.gettempdir()) / "test_pcheck4.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result

        result = _run_instance_core("i1", config, mock_writer)

        mock_writer.record_error.assert_called_once()
        call = mock_writer.record_error.call_args
        assert call.kwargs["error_type"] == "docker_start_failed"
        mock_docker.stop.assert_not_called()

    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    @patch("src.pipeline_check.plan_agent.run")
    @patch("src.pipeline_check.check_agent.run")
    @patch("src.pipeline_check.code_agent.run")
    @patch("src.pipeline_check.evaluate")
    @patch("src.pipeline_check.save_trajectory")
    @patch("src.pipeline_check.load_aggregated_rules")
    @patch("src.pipeline_check.format_rules_for_prompt")
    def test_check_agent_failure_fallback(
        self,
        mock_format_rules,
        mock_load_rules,
        mock_save_traj,
        mock_eval,
        mock_code,
        mock_check,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
        tmp_path,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "r/r",
            "problem_statement": "p",
            "image_name": "img",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_check.side_effect = TaskError("Check agent crashed")
        mock_code.return_value = ("diff", [])
        mock_eval.return_value = {"resolved": True, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "traj.json"
        mock_load_rules.return_value = {"always": [], "branches": []}
        mock_format_rules.return_value = ""

        mock_writer = MagicMock()

        tmp_result = tmp_path / "test_pcheck5.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        out_dir = tmp_path / "output" / "test"
        out_dir.mkdir(parents=True)
        mock_writer.output_dir = str(out_dir)
        plan_path = out_dir / "plans" / "plan.md"
        plan_path.parent.mkdir(parents=True)
        mock_writer.save_plan.return_value = plan_path

        result = _run_instance_core("i1", config, mock_writer)

        # Even when check fails, code should still run
        mock_code.assert_called_once()
        mock_eval.assert_called_once()
        assert result["check_result"]["passed"] is False
        assert "_check_error" in result["check_result"]
        assert result["test_results"]["resolved"] is True

    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    @patch("src.pipeline_check.plan_agent.run")
    def test_taskerror_in_plan_stops_pipeline(
        self,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
        tmp_path,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "r/r",
            "problem_statement": "p",
            "image_name": "img",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.side_effect = TaskError("Plan generation failed")

        mock_writer = MagicMock()

        tmp_result = tmp_path / "test_pcheck6.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        out_dir = tmp_path / "output" / "test"
        out_dir.mkdir(parents=True)
        mock_writer.output_dir = str(out_dir)

        result = _run_instance_core("i1", config, mock_writer)

        mock_writer.record_error.assert_called_once()
        assert result.get("check_result") == {} or result.get("check_result") is not None


class TestRunInstanceWrapper:
    @patch("src.pipeline_check.OutputWriter")
    @patch("src.pipeline_check._run_instance_core")
    def test_run_instance_returns_result(self, mock_core, mock_writer_cls, config):
        mock_core.return_value = {
            "check_result": {"passed": True, "violations": []},
            "test_results": {"resolved": True},
            "resolved": True,
        }
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        result = run_instance("i1", config)

        assert result["resolved"] is True
        mock_writer_cls.assert_called_once()

    @patch("src.pipeline_check.OutputWriter")
    @patch("src.pipeline_check._run_instance_core")
    def test_fatalerror_triggers_emergency_save(self, mock_core, mock_writer_cls, config):
        mock_core.side_effect = FatalError("API error")
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        with pytest.raises(FatalError, match="API error"):
            run_instance("i1", config)

        mock_writer.emergency_save.assert_called_once()


class TestRulesLoadFailure:
    @patch("src.pipeline_check.DockerEnvWrapper")
    @patch("src.pipeline_check.InstanceLoader")
    @patch("src.pipeline_check.plan_agent.run")
    @patch("src.pipeline_check.check_agent.run")
    @patch("src.pipeline_check.code_agent.run")
    @patch("src.pipeline_check.evaluate")
    @patch("src.pipeline_check.save_trajectory")
    @patch("src.pipeline_check.load_aggregated_rules")
    def test_rules_load_failure_continues_without_rules(
        self,
        mock_load_rules,
        mock_save_traj,
        mock_eval,
        mock_code,
        mock_check,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
        tmp_path,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "r/r",
            "problem_statement": "p",
            "image_name": "img",
            "repo_path": "/repo",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_check.return_value = (
            {"passed": True, "violations": [], "overall_assessment": ""},
            [],
        )
        mock_code.return_value = ("diff", [])
        mock_eval.return_value = {"resolved": True, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "traj.json"
        mock_load_rules.side_effect = FileNotFoundError("Rules file missing")

        mock_writer = MagicMock()

        tmp_result = tmp_path / "test_pcheck7.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        out_dir = tmp_path / "output" / "test"
        out_dir.mkdir(parents=True)
        mock_writer.output_dir = str(out_dir)
        plan_path = out_dir / "plans" / "plan.md"
        plan_path.parent.mkdir(parents=True)
        mock_writer.save_plan.return_value = plan_path

        result = _run_instance_core("i1", config, mock_writer)

        # Pipeline should continue even when rules fail to load
        mock_check.assert_called_once()
        assert result["resolved"] is True
