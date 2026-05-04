"""Tests for src/agents/reflect_agent.py."""

from unittest.mock import patch

import pytest

from src.agents import reflect_agent
from src.agents.reflect_agent import NullEnvironment, _format_feedback
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


class MockLiteLLMModel:
    def __init__(self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking

    def query(self, messages: list[dict[str, str]]) -> dict:
        return {
            "content": (
                "## Improved Plan\n\n1. Analyze the bug\n"
                "2. Fix the parser\n3. Add tests\n\n"
                "This is a detailed improved plan with specific steps."
            ),
            "extra": {},
        }


class MockLiteLLMModelEmpty(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "", "extra": {}}


class MockLiteLLMModelShort(MockLiteLLMModel):
    def query(self, messages: list[dict[str, str]]) -> dict:
        return {"content": "Too short.", "extra": {}}


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            use_gepa_reflection_prompt=True,
        ),
        prompts=PromptConfig(
            plan_optimization_prompt="Optimize the plan.",
            plan_format_template="## Analysis\n## Steps",
        ),
        agent=AgentConfig(max_steps=20),
        deepseek_api_key="test-key",
    )


@pytest.fixture
def feedback_data() -> dict:
    return {
        "meta": {
            "optimization_info_level": 1,
            "target_plan_number": 3,
            "current_round": 2,
            "model": "deepseek-v4-flash",
            "use_gepa_reflection_prompt": True,
        },
        "original_prompt": "Fix the bug",
        "current_plan": {
            "content": "Current plan to fix the bug.",
            "plan_id": "plan_001",
            "round_generated": 1,
        },
        "trajectories": {
            "plan_generation_trajectory_path": "t1.json",
            "code_generation_trajectory_path": "t2.json",
            "reflection_trajectory_path": None,
        },
        "generated_code": {
            "patch_path": "patch.patch",
            "content": "diff --git a/file.py",
        },
        "test_results": {
            "resolved": False,
            "stdout": "FAILED",
            "stderr": "Error",
            "log_dir": "logs/",
        },
        "error_info": None,
    }


class TestNullEnvironment:
    def test_execute_returns_empty_dict(self):
        env = NullEnvironment()
        assert env.execute("ls") == {"output": "", "returncode": 0}

    def test_get_template_vars_returns_empty(self):
        env = NullEnvironment()
        assert env.get_template_vars() == {}

    def test_all_methods_compatible(self):
        """Verify NullEnvironment methods don't crash."""
        env = NullEnvironment()
        env.execute("cmd")
        env.get_template_vars()


class TestRunWithGepa:
    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_returns_plan_and_messages(self, mock_import, config, feedback_data):
        mock_import.return_value = (object, MockLiteLLMModel, object)
        plan, messages = reflect_agent.run(config, feedback_data)

        assert len(plan) > 50
        assert "Improved Plan" in plan or "Analyze" in plan
        assert len(messages) == 3
        assert messages[-1]["role"] == "assistant"

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_uses_gepa_template_when_enabled(self, mock_import, config, feedback_data):
        mock_import.return_value = (object, MockLiteLLMModel, object)
        reflect_agent.run(config, feedback_data)


class TestRunWithSimplifiedPrompt:
    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_uses_simplified_prompt_when_disabled(self, mock_import, feedback_data):
        config = Config(
            system=SystemConfig(
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                use_gepa_reflection_prompt=False,
            ),
            prompts=PromptConfig(
                plan_optimization_prompt="Simplified optimize prompt.",
            ),
            agent=AgentConfig(max_steps=20),
            deepseek_api_key="test-key",
        )
        mock_import.return_value = (object, MockLiteLLMModel, object)
        plan, _ = reflect_agent.run(config, feedback_data)
        assert len(plan) > 50


class TestRunValidation:
    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_empty_output_raises_task_error(self, mock_import, config, feedback_data):
        mock_import.return_value = (object, MockLiteLLMModelEmpty, object)
        with pytest.raises(TaskError, match="empty"):
            reflect_agent.run(config, feedback_data)

    @patch("src.agents.reflect_agent.import_minisweagent")
    def test_short_output_raises_task_error(self, mock_import, config, feedback_data):
        mock_import.return_value = (object, MockLiteLLMModelShort, object)
        with pytest.raises(TaskError, match="too short"):
            reflect_agent.run(config, feedback_data)


class TestFormatFeedback:
    def test_includes_round_and_plan(self, feedback_data):
        text = _format_feedback(feedback_data)
        assert "Round: 2" in text
        assert "Current Plan:" in text
        assert "Current plan to fix the bug." in text

    def test_includes_test_results(self, feedback_data):
        text = _format_feedback(feedback_data)
        assert "Test Results:" in text
        assert "Resolved: False" in text
        assert "FAILED" in text

    def test_handles_missing_test_results(self):
        minimal = {
            "meta": {"current_round": 1},
            "current_plan": {"content": "Plan"},
        }
        text = _format_feedback(minimal)
        assert "Plan" in text
        assert "Test Results:" not in text


class TestMissingDependency:
    @patch(
        "src.agents.reflect_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, feedback_data):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            reflect_agent.run(config, feedback_data)
