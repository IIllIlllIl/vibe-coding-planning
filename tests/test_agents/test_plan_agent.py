"""Tests for src/agents/plan_agent.py."""

from unittest.mock import MagicMock, patch

import pytest

from src.agents import plan_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import FatalError, TaskError


class MockDefaultAgent:
    """Mock that mimics mini-swe-agent DefaultAgent."""

    def __init__(self, system_prompt: str, model, environment, max_steps: int = 30):
        self.system_prompt = system_prompt
        self.model = model
        self.environment = environment
        self.max_steps = max_steps
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "test issue"},
            {"role": "assistant", "content": "plan output"},
        ]

    def run(self, user_message: str) -> str:
        return "plan output"


class MockLiteLLMModel:
    def __init__(self, model: str, api_key: str, api_base: str = ""):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
        ),
        prompts=PromptConfig(
            plan_generation_prompt="You are a planner.",
            plan_format_template="## Analysis\n## Steps",
        ),
        agent=AgentConfig(max_steps=25),
        deepseek_api_key="test-key",
    )


@pytest.fixture
def mock_env():
    return MagicMock()


class TestRunSuccess:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_returns_plan_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan, messages = plan_agent.run(config, "Fix parser bug", mock_env)

        assert plan == "plan output"
        assert len(messages) == 3
        assert messages[0]["role"] == "system"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_system_prompt_contains_plan_generation_text(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_lite_llm_model_configured_correctly(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_agent_receives_max_steps(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_environment_passed_to_agent(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_plan_trimmed(self, mock_import, config, mock_env):
        class AgentWithSpaces(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "  plan with spaces  "

        mock_import.return_value = (AgentWithSpaces, MockLiteLLMModel)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "plan with spaces"


class TestRunValidation:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_empty_plan_raises_task_error(self, mock_import, config, mock_env):
        class EmptyAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return ""

        mock_import.return_value = (EmptyAgent, MockLiteLLMModel)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_whitespace_only_plan_raises_task_error(self, mock_import, config, mock_env):
        class WhitespaceAgent(MockDefaultAgent):
            def run(self, user_message: str) -> str:
                return "   \n\n   "

        mock_import.return_value = (WhitespaceAgent, MockLiteLLMModel)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)


class TestMissingDependency:
    @patch(
        "src.agents.plan_agent.import_minisweagent",
        side_effect=FatalError("mini-swe-agent is not installed"),
    )
    def test_missing_import_raises_fatal_error(self, mock_import, config, mock_env):
        with pytest.raises(FatalError, match="mini-swe-agent"):
            plan_agent.run(config, "Fix parser bug", mock_env)


class TestAgentDoesNotHoldEnvReference:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_function_returns_no_env(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel)
        plan, messages = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan is not None
        assert messages is not None
