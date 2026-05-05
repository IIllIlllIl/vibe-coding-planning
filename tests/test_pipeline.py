"""Tests for src/pipeline.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from typing import Any
import pytest
import json

from src.config import AgentConfig, Config, DockerConfig, EvaluatorConfig, PromptConfig, SystemConfig
from src.pipeline import run_instance


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            n=3,
            swe_pro_instances=["astropy__astropy-14539"],
            output_dir="./output",
            use_gepa_reflection_prompt=True,
        ),
        prompts=PromptConfig(
            plan_generation_prompt="Plan prompt.",
            code_generation_prompt="Code prompt.",
            plan_optimization_prompt="Optimize prompt.",
        ),
        docker=DockerConfig(
            image_builder_script="./scripts/build.sh",
            workdir="/testbed",
        ),
        agent=AgentConfig(max_steps=10),
        deepseek_api_key="test-key",
    )


class TestPipelineSingleRound:
    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_single_round_calls_all_stages(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_reflect,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        config_single = Config(
            system=SystemConfig(
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                n=1,
                swe_pro_instances=["astropy__astropy-14539"],
                output_dir="./output",
                use_gepa_reflection_prompt=True,
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            deepseek_api_key="test-key",
        )

        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan content", [{"role": "system", "content": "plan"}])
        mock_code.return_value = ("diff --git\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "trajectory.json"

        mock_writer = MagicMock()
        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_result.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("astropy__astropy-14539", config_single)

        mock_docker.start.assert_called_once()
        mock_plan.assert_called_once()
        mock_code.assert_called_once()
        mock_eval.assert_called_once()
        mock_writer.save_round.assert_called_once()
        mock_docker.stop.assert_called_once()
        mock_writer.finalize.assert_called_once()


class TestPipelineMultiRound:
    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_three_rounds_calls_reflect_twice(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_reflect,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan 1", [])
        mock_reflect.return_value = ("Plan improved", [])
        mock_code.return_value = ("diff --git\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "trajectory.json"

        mock_writer = MagicMock()
        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_result2.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("astropy__astropy-14539", config)

        assert mock_plan.call_count == 1
        assert mock_reflect.call_count == 2  # rounds 2 and 3
        assert mock_code.call_count == 3
        assert mock_eval.call_count == 3
        assert mock_writer.save_round.call_count == 3
        mock_docker.stop.assert_called_once()


class TestPipelineErrorHandling:
    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_taskerror_in_round_continues_to_next_round(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_reflect,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        config_single = Config(
            system=SystemConfig(
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                n=3,
                swe_pro_instances=["astropy__astropy-14539"],
                output_dir="./output",
                use_gepa_reflection_prompt=True,
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            deepseek_api_key="test-key",
        )

        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader
        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        from src.exceptions import TaskError

        mock_plan.return_value = ("Plan 1", [])
        mock_reflect.return_value = ("Plan improved", [])
        # Round 2 code agent fails, round 3 succeeds
        code_call_count = [0]
        def code_side_effect(cfg, plan, issue, env):
            code_call_count[0] += 1
            if code_call_count[0] == 2:  # second call = round 2
                raise TaskError("Code generation failed")
            return ("diff --git\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", [])

        mock_code.side_effect = code_side_effect
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "trajectory.json"

        mock_writer = MagicMock()
        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_result3.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("astropy__astropy-14539", config_single)

        # Should still complete all rounds, with round 2 recording an error
        assert mock_writer.save_round.call_count == 2  # round 1 and 3
        mock_writer.record_error.assert_called_once()
        mock_docker.stop.assert_called_once()

    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.OutputWriter")
    def test_fatalerror_triggers_emergency_save(
        self,
        mock_writer_cls,
        mock_reflect,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader
        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        from src.exceptions import FatalError

        mock_plan.side_effect = FatalError("API rate limited")

        mock_writer = MagicMock()
        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_result4.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        with pytest.raises(FatalError, match="API rate limited"):
            run_instance("astropy__astropy-14539", config)

        mock_writer.emergency_save.assert_called_once()

    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.OutputWriter")
    def test_early_fatalerror_saves_data_with_run_id(
        self,
        mock_writer_cls,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        """Simulate FatalError before any round completes; verify emergency_save output."""
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader
        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        from src.exceptions import FatalError

        # Simulate FatalError during plan generation (round 1)
        mock_plan.side_effect = FatalError("API key invalid")

        # Capture what emergency_save writes
        saved_data = {}

        class TrackingWriter:
            def __init__(self, *args, **kwargs):
                self.plans = []
                self.errors = []
                self.run_id = "test_run"

            def save_round(self, **kwargs):
                self.plans.append(kwargs)

            def record_error(self, **kwargs):
                self.errors.append(kwargs)

            def finalize(self, **kwargs):
                return None

            def emergency_save(self):
                saved_data["run_id"] = self.run_id
                saved_data["plans"] = self.plans
                saved_data["errors"] = self.errors
                return None

        mock_writer_cls.return_value = TrackingWriter()

        with pytest.raises(FatalError, match="API key invalid"):
            run_instance("astropy__astropy-14539", config)

        assert "run_id" in saved_data
        assert saved_data["run_id"] == "test_run"
        assert "plans" in saved_data
        assert "errors" in saved_data


class TestPipelineDockerLifecycle:
    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_docker_start_stop_called_once(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_reflect,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
        config,
    ):
        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "astropy__astropy-14539",
            "repo": "astropy/astropy",
            "problem_statement": "Fix the parser bug",
            "image_name": "swebench/astropy-astropy:latest",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_reflect.return_value = ("Plan improved", [])
        mock_code.return_value = ("diff --git\n--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "trajectory.json"

        mock_writer = MagicMock()
        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_result5.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("astropy__astropy-14539", config)

        mock_docker.start.assert_called_once()
        mock_docker.stop.assert_called_once()


class TestPipelineEvaluatorTimeout:
    """Verify pipeline forwards config.evaluator.timeout to evaluate()."""

    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.reflect_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_evaluator_timeout_propagated(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_reflect,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
    ):
        config = Config(
            system=SystemConfig(
                model="m", api_base="https://x.y", n=1,
                swe_pro_instances=["i1"], output_dir="./output",
                use_gepa_reflection_prompt=True,
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            evaluator=EvaluatorConfig(timeout=999),
            deepseek_api_key="k",
        )

        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1", "repo": "x/y",
            "problem_statement": "p", "image_name": "img",
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_code.return_value = ("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "traj.json"

        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_eval_timeout.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer = MagicMock()
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("i1", config)

        # The kwarg must come through as 999 (not 300, not the default)
        _, call_kwargs = mock_eval.call_args
        assert call_kwargs.get("timeout") == 999, (
            f"Expected timeout=999 but got call_kwargs={call_kwargs}"
        )


class TestPipelineUsesDeriveImageName:
    """Verify pipeline uses derive_image_name (consistent with evaluator)."""

    @patch("src.pipeline.DockerEnvWrapper")
    @patch("src.pipeline.InstanceLoader")
    @patch("src.pipeline.plan_agent.run")
    @patch("src.pipeline.code_agent.run")
    @patch("src.pipeline.evaluate")
    @patch("src.pipeline.save_trajectory")
    @patch("src.pipeline.OutputWriter")
    def test_instance_id_used_for_image_name(
        self,
        mock_writer_cls,
        mock_save_traj,
        mock_eval,
        mock_code,
        mock_plan,
        mock_loader_cls,
        mock_docker_cls,
    ):
        """instance_info without explicit image_name must derive image
        from instance_id using the SWE-bench official naming convention."""
        config = Config(
            system=SystemConfig(
                model="m", api_base="https://x.y", n=1,
                swe_pro_instances=["i1"], output_dir="./output",
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            evaluator=EvaluatorConfig(),
            deepseek_api_key="k",
        )

        mock_loader = MagicMock()
        mock_loader.load_instance.return_value = {
            "instance_id": "i1",
            "repo": "pandas-dev/pandas",   # contains a slash
            "problem_statement": "p",
            # NB: no image_name, force derivation path
        }
        mock_loader_cls.return_value = mock_loader

        mock_docker = MagicMock()
        mock_docker_cls.return_value = mock_docker

        mock_plan.return_value = ("Plan", [])
        mock_code.return_value = ("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b", [])
        mock_eval.return_value = {"resolved": False, "stdout": "", "stderr": "", "log_dir": ""}
        mock_save_traj.return_value = MagicMock()
        mock_save_traj.return_value.__str__ = lambda self: "traj.json"

        from pathlib import Path
        import tempfile
        tmp_result = Path(tempfile.gettempdir()) / "test_derive.json"
        tmp_result.write_text('{"plans": [], "run_id": "test"}', encoding="utf-8")
        mock_writer = MagicMock()
        mock_writer.finalize.return_value = tmp_result
        mock_writer_cls.return_value = mock_writer

        run_instance("i1", config)

        _, kwargs = mock_docker.start.call_args
        assert kwargs["image"] == "swebench/sweb.eval.x86_64.i1:latest", (
            f"Expected swebench/sweb.eval.x86_64.i1:latest but got {kwargs['image']!r}"
        )
