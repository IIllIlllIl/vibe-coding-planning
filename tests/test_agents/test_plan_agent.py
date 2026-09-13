"""Tests for src/agents/plan_agent.py."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.agents import plan_agent
from src.config import AgentConfig, Config, PromptConfig, SystemConfig
from src.exceptions import AgentTaskError, FatalError, TaskError


DIRECT_PLAN_TEXT = (
    "# Plan\n\n"
    "## Navigation (N)\nInspect `file.py`.\n\n"
    "## Reproduction (R)\nRun the focused reproduction.\n\n"
    "## Patch (P)\nUpdate the affected branch.\n\n"
    "## Validation (V)\nRun the focused test."
)

DIRECT_MARKDOWN_TEXT = (
    "# Plan\n\n"
    "Update the parser's token handling in `src/parser.py` while preserving "
    "the existing public behavior.\n\n"
    "## Validation\nRun the focused parser regression test."
)

BOUNDED_MARKDOWN_PLAN = DIRECT_MARKDOWN_TEXT + "\n"
BOUNDED_MARKDOWN_RAW = (
    "FINAL_PLAN\n"
    + BOUNDED_MARKDOWN_PLAN
    + "END_PLAN\n"
    + "Here is another explanation.</parameter>"
)
HUMAN_BOUNDED_MARKDOWN_RAW = (
    "START_PLAN\n"
    + BOUNDED_MARKDOWN_PLAN
    + "END_PLAN\n"
    + "Provider trailer retained only as raw evidence.</parameter>"
)


class MockLiteLLMModel:
    def __init__(self, *, model_name: str, model_kwargs: dict, cost_tracking: str = "ignore_errors"):
        self.model_name = model_name
        self.model_kwargs = model_kwargs
        self.cost_tracking = cost_tracking


class MockDefaultAgent:
    """Simulates a DefaultAgent that successfully submits a plan."""

    last_kwargs: dict = {}
    last_run_kwargs: dict = {}

    def __init__(self, model, env, **kwargs):
        self.model = model
        self.env = env
        MockDefaultAgent.last_kwargs = kwargs
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "plan output"},
        ]

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "plan output")


class MockDefaultAgentEmpty(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "")


class MockDefaultAgentWhitespace(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "   \n\n   ")


class MockDefaultAgentSpaces(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("Submitted", "  plan with spaces  ")


class MockDefaultAgentDirect(MockDefaultAgent):
    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        plan = DIRECT_PLAN_TEXT
        self.messages[-1]["content"] = "FINAL_PLAN\n" + plan
        return ("Submitted", plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + plan)


class MockDefaultAgentDirectTrailingWhitespace(MockDefaultAgent):
    def run(self, **kwargs):
        plan = DIRECT_PLAN_TEXT + "\n\n"
        self.messages[-1]["content"] = "FINAL_PLAN\n" + plan
        return "Submitted", plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + plan


class MockDefaultAgentDirectMarkdown(MockDefaultAgent):
    def run(self, **kwargs):
        self.messages[-1]["content"] = "FINAL_PLAN\n" + DIRECT_MARKDOWN_TEXT
        return (
            "Submitted",
            plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + DIRECT_MARKDOWN_TEXT,
        )


class MockDefaultAgentDirectBoundedMarkdown(MockDefaultAgent):
    def run(self, **kwargs):
        self.messages[-1]["content"] = BOUNDED_MARKDOWN_RAW
        return (
            "Submitted",
            plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX
            + BOUNDED_MARKDOWN_RAW,
        )


class MockDefaultAgentDirectHumanBoundedMarkdown(MockDefaultAgent):
    def run(self, **kwargs):
        self.messages[-1]["content"] = HUMAN_BOUNDED_MARKDOWN_RAW
        return (
            "Submitted",
            plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX
            + HUMAN_BOUNDED_MARKDOWN_RAW,
        )


class MockDefaultAgentLimitExceeded(MockDefaultAgent):
    """Simulates step-limit exhaustion — falls back to last assistant message."""

    def run(self, **kwargs):
        MockDefaultAgent.last_run_kwargs = kwargs
        return ("LimitsExceeded", "step limit reached")


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
        ),
        prompts=PromptConfig(
            plan_generation_prompt="You are a planner.\n{{nrpv_block}}",
            plan_instance_template="<pr_description>{{task}}</pr_description>",
            nrpv_block="## Navigation\n## Reproduction\n## Patch\n## Validation",
        ),
        agent=AgentConfig(max_steps=25),
        api_key="test-key",
    )


@pytest.fixture
def mock_env():
    return object()


class TestRunSuccess:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_returns_plan_and_messages(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan, messages = plan_agent.run(config, "Fix parser bug", mock_env)

        assert plan == "plan output"
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_system_template_passed_verbatim(self, mock_import, config, mock_env):
        """The plan_generation_prompt must be forwarded to DefaultAgent
        unchanged — no host-side str.format / str.replace. The
        ``{{nrpv_block}}`` Jinja placeholder is rendered by
        mini-swe-agent's DefaultAgent at run() time.
        """
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan_agent.run(config, "Fix parser bug", mock_env)

        st = MockDefaultAgent.last_kwargs["system_template"]
        # Placeholder preserved verbatim.
        assert "{{nrpv_block}}" in st
        # NRPV content is NOT inlined into the template source.
        assert "## Navigation" not in st

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_nrpv_block_injected_via_agent_run(self, mock_import, config, mock_env):
        """The NRPV definition must flow to mini-swe-agent as a
        ``run(**kwargs)`` value, not as inlined template source. This
        keeps the second-pass Jinja render safe even when NRPV content
        ever ends up containing Jinja-looking fragments."""
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        plan_agent.run(config, "Fix parser bug", mock_env)

        assert MockDefaultAgent.last_run_kwargs["task"] == "Fix parser bug"
        assert (
            MockDefaultAgent.last_run_kwargs["nrpv_block"]
            == config.prompts.nrpv_block
        )

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_plan_not_trimmed_by_host(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentSpaces, MockLiteLLMModel, object)
        plan, _ = plan_agent.run(config, "Fix parser bug", mock_env)
        assert plan == "  plan with spaces  "

    def test_direct_plan_terminal_preserves_markdown_without_shell(self):
        class SubmittedForTest(Exception):
            pass

        class BaseAgent:
            def get_observation(self, _response):
                raise AssertionError("shell parser must not receive FINAL_PLAN")

        agent = plan_agent._direct_plan_agent_class(
            BaseAgent, SubmittedForTest
        )()
        text = "# Plan\n\n```python\nprint('$() and `ticks`')\n```\n"
        with pytest.raises(SubmittedForTest) as raised:
            agent.get_observation({"content": "FINAL_PLAN\n" + text})

        assert str(raised.value) == (
            plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + text
        )

    def test_nonterminal_response_still_uses_shell_action_path(self):
        class SubmittedForTest(Exception):
            pass

        class BaseAgent:
            def get_observation(self, response):
                return {"received": response["content"]}

        agent = plan_agent._direct_plan_agent_class(
            BaseAgent, SubmittedForTest
        )()
        assert agent.get_observation({"content": "```bash\npwd\n```"}) == {
            "received": "```bash\npwd\n```"
        }

    def test_bounded_terminal_response_bypasses_shell_and_preserves_raw_text(self):
        class SubmittedForTest(Exception):
            pass

        class BaseAgent:
            def get_observation(self, _response):
                raise AssertionError("shell parser must not receive FINAL_PLAN")

        agent = plan_agent._direct_plan_agent_class(
            BaseAgent,
            SubmittedForTest,
            bounded=True,
        )()
        with pytest.raises(SubmittedForTest) as raised:
            agent.get_observation({"content": BOUNDED_MARKDOWN_RAW})

        assert str(raised.value) == (
            plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX
            + BOUNDED_MARKDOWN_RAW
        )

    def test_human_bounded_terminal_uses_start_plan_interception(self):
        class SubmittedForTest(Exception):
            pass

        class BaseAgent:
            def get_observation(self, _response):
                raise AssertionError("shell parser must not receive START_PLAN")

        agent = plan_agent._direct_plan_agent_class(
            BaseAgent,
            SubmittedForTest,
            bounded=True,
            start_marker=plan_agent.DIRECT_HUMAN_PLAN_MARKER,
        )()
        with pytest.raises(SubmittedForTest) as raised:
            agent.get_observation({"content": HUMAN_BOUNDED_MARKDOWN_RAW})

        assert str(raised.value) == (
            plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX
            + HUMAN_BOUNDED_MARKDOWN_RAW
        )

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_limit_exceeded_raises_task_error(self, mock_import, config, mock_env):
        """When DefaultAgent hits a limit without submitting, raise TaskError."""
        mock_import.return_value = (MockDefaultAgentLimitExceeded, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="terminated without a submission"):
            plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_requires_direct_terminal_submission(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (MockDefaultAgent, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="direct FINAL_PLAN"):
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
            )

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_returns_exact_direct_plan(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirect,
            MockLiteLLMModel,
            object,
        )
        plan, _ = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
        )
        assert plan == DIRECT_PLAN_TEXT
        system = MockDefaultAgent.last_kwargs["system_template"]
        assert "Planner action and final-submission protocol" in system
        assert "Mini-swe action protocol" not in system
        assert "FINAL_PLAN\n# Plan\n" in system
        assert "must contain no text outside that structure" in system
        assert "malformed terminal response is rejected" in system

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_preserves_valid_plan_trailing_whitespace(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirectTrailingWhitespace,
            MockLiteLLMModel,
            object,
        )
        plan, _ = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
        )
        assert plan == DIRECT_PLAN_TEXT + "\n\n"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_accepts_flexible_markdown_without_nrpv(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirectMarkdown,
            MockLiteLLMModel,
            object,
        )
        plan, _ = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
            direct_submission_protocol=plan_agent.DIRECT_MARKDOWN_PROTOCOL,
        )

        assert plan == DIRECT_MARKDOWN_TEXT
        system = MockDefaultAgent.last_kwargs["system_template"]
        assert "No fixed subsection headings are required" in " ".join(system.split())
        assert "[The complete standalone Markdown Plan" not in system
        assert "## Navigation (N)" not in system

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_template_protocol_shows_positive_replaceable_example(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirectMarkdown,
            MockLiteLLMModel,
            object,
        )

        plan, _ = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
            direct_submission_protocol=(plan_agent.DIRECT_MARKDOWN_TEMPLATE_PROTOCOL),
        )

        assert plan == DIRECT_MARKDOWN_TEXT
        system = MockDefaultAgent.last_kwargs["system_template"]
        normalized = " ".join(system.split())
        assert "FINAL_PLAN\n# Plan\n\n[[TASK_SPECIFIC_MARKDOWN_PLAN]]" in system
        assert "Remove the placeholder itself" in system
        assert "the Host validates but never edits the response" in normalized
        assert "XML or DSML tag" in normalized
        assert "simulated observation" in normalized
        assert "## Navigation (N)" not in system

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_bounded_markdown_uses_only_text_before_end_marker(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirectBoundedMarkdown,
            MockLiteLLMModel,
            object,
        )

        plan, messages = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
            direct_submission_protocol=(
                plan_agent.DIRECT_BOUNDED_MARKDOWN_PROTOCOL
            ),
        )

        assert plan == BOUNDED_MARKDOWN_PLAN
        assert plan_agent.direct_plan_terminal_response(messages) == (
            BOUNDED_MARKDOWN_RAW
        )
        assert "another explanation" not in plan
        assert "</parameter>" not in plan
        system = MockDefaultAgent.last_kwargs["system_template"]
        assert (
            "FINAL_PLAN\n# Plan\n\n[[TASK_SPECIFIC_MARKDOWN_PLAN]]\nEND_PLAN"
            in system
        )
        assert "only the exact text between" in " ".join(system.split())

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_human_bounded_markdown_uses_semantic_markers_and_direct_audience(
        self, mock_import, config, mock_env
    ):
        mock_import.return_value = (
            MockDefaultAgentDirectHumanBoundedMarkdown,
            MockLiteLLMModel,
            object,
        )

        plan, messages = plan_agent.run(
            config,
            "Fix parser bug",
            mock_env,
            require_direct_submission=True,
            direct_submission_protocol=(
                plan_agent.DIRECT_HUMAN_BOUNDED_MARKDOWN_PROTOCOL
            ),
        )

        assert plan == BOUNDED_MARKDOWN_PLAN
        assert plan_agent.direct_plan_terminal_response(
            messages,
            start_marker=plan_agent.DIRECT_HUMAN_PLAN_MARKER,
        ) == HUMAN_BOUNDED_MARKDOWN_RAW
        assert "Provider trailer" not in plan
        system = MockDefaultAgent.last_kwargs["system_template"]
        normalized = " ".join(system.split())
        assert (
            "START_PLAN\n# Plan\n\n[[TASK_SPECIFIC_MARKDOWN_PLAN]]\nEND_PLAN"
            in system
        )
        assert "marks where the Plan begins" in normalized
        assert "shown directly and verbatim to the human developer" in normalized
        assert "only text treated as the Plan" in normalized

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_human_bounded_markdown_rejects_missing_end_marker(
        self, mock_import, config, mock_env
    ):
        class MissingHumanEndMarker(MockDefaultAgent):
            def run(self, **kwargs):
                raw = "START_PLAN\n" + DIRECT_MARKDOWN_TEXT
                self.messages[-1]["content"] = raw
                return (
                    "Submitted",
                    plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX + raw,
                )

        mock_import.return_value = (
            MissingHumanEndMarker,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(AgentTaskError, match="missing an exact 'END_PLAN'"):
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
                direct_submission_protocol=(
                    plan_agent.DIRECT_HUMAN_BOUNDED_MARKDOWN_PROTOCOL
                ),
            )


class TestRunValidation:
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_bounded_markdown_rejects_protocol_residue_inside_boundary(
        self, mock_import, config, mock_env
    ):
        class ResidueBeforeEnd(MockDefaultAgent):
            def run(self, **kwargs):
                raw = (
                    "FINAL_PLAN\n"
                    + DIRECT_MARKDOWN_TEXT
                    + "\n</parameter>\nEND_PLAN"
                )
                self.messages[-1]["content"] = raw
                return (
                    "Submitted",
                    plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX + raw,
                )

        mock_import.return_value = (
            ResidueBeforeEnd,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(AgentTaskError, match="tool-protocol residue") as caught:
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
                direct_submission_protocol=(
                    plan_agent.DIRECT_BOUNDED_MARKDOWN_PROTOCOL
                ),
            )

        assert caught.value.reason == "plan_invalid_markdown"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_bounded_markdown_rejects_missing_end_marker(
        self, mock_import, config, mock_env
    ):
        class MissingEndMarker(MockDefaultAgent):
            def run(self, **kwargs):
                raw = "FINAL_PLAN\n" + DIRECT_MARKDOWN_TEXT
                self.messages[-1]["content"] = raw
                return (
                    "Submitted",
                    plan_agent._DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX + raw,
                )

        mock_import.return_value = (
            MissingEndMarker,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(AgentTaskError, match="missing an exact 'END_PLAN'") as caught:
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
                direct_submission_protocol=(
                    plan_agent.DIRECT_BOUNDED_MARKDOWN_PROTOCOL
                ),
            )

        assert caught.value.reason == "plan_invalid_markdown"
        assert caught.value.trajectory[-1]["content"].startswith("FINAL_PLAN\n")

    def test_description_text_is_not_blanket_rejected_as_protocol_residue(self):
        plan = "# Plan\n\nUpdate the literal `</description>` parsing behavior."

        assert (
            plan_agent._direct_plan_markdown_error(
                plan,
                require_nrpv=False,
                forbidden_placeholder=plan_agent.DIRECT_MARKDOWN_PLAN_PLACEHOLDER,
            )
            is None
        )

    @pytest.mark.parametrize(
        ("plan", "message"),
        [
            ("plain text", "must start"),
            (
                "# Plan\n\n## Navigation (N)\nx\n\n## Reproduction (R)\nx\n\n"
                "## Patch (P)\nx\n\n## Validation (V)\nx\n</parameter>",
                "protocol residue",
            ),
            (
                "# Plan\n\n## Navigation (N)\nx\n\n## Reproduction (R)\nx\n\n"
                "## Patch (P)\nx\n\n## Validation (V)\n",
                "section is empty",
            ),
        ],
    )
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_safe_pce_rejects_invalid_markdown_without_rewriting(
        self, mock_import, plan, message, config, mock_env
    ):
        class InvalidDirect(MockDefaultAgent):
            def run(self, **kwargs):
                self.messages[-1]["content"] = "FINAL_PLAN\n" + plan
                return "Submitted", plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + plan

        mock_import.return_value = (InvalidDirect, MockLiteLLMModel, object)
        with pytest.raises(AgentTaskError, match=message) as caught:
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
            )
        assert caught.value.reason == "plan_invalid_markdown"
        assert caught.value.trajectory[-1]["content"] == "FINAL_PLAN\n" + plan

    @pytest.mark.parametrize(
        ("plan", "message"),
        [
            (
                "# Plan\n\n[[TASK_SPECIFIC_MARKDOWN_PLAN]]",
                "unexpanded template placeholder",
            ),
            (
                "# Plan\nUseful task-specific plan.\n</description>\nExtra text.",
                "protocol residue",
            ),
        ],
    )
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_template_markdown_rejects_unexpanded_or_protocol_text(
        self, mock_import, plan, message, config, mock_env
    ):
        class InvalidTemplateMarkdown(MockDefaultAgent):
            def run(self, **kwargs):
                self.messages[-1]["content"] = "FINAL_PLAN\n" + plan
                return "Submitted", plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + plan

        mock_import.return_value = (
            InvalidTemplateMarkdown,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(AgentTaskError, match=message) as caught:
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
                direct_submission_protocol=(
                    plan_agent.DIRECT_MARKDOWN_TEMPLATE_PROTOCOL
                ),
            )

        assert caught.value.reason == "plan_invalid_markdown"
        assert caught.value.trajectory[-1]["content"] == "FINAL_PLAN\n" + plan

    @pytest.mark.parametrize(
        ("plan", "message"),
        [
            ("# Plan\n", "no substantive content"),
            ("# Plan\nUseful plan.\n# Plan\nDuplicate.", "exactly one"),
            ("# Plan\nUseful plan.</parameter>", "protocol residue"),
        ],
    )
    @patch("src.agents.plan_agent.import_minisweagent")
    def test_flexible_markdown_rejects_only_outer_contract_failures(
        self, mock_import, plan, message, config, mock_env
    ):
        class InvalidDirectMarkdown(MockDefaultAgent):
            def run(self, **kwargs):
                self.messages[-1]["content"] = "FINAL_PLAN\n" + plan
                return "Submitted", plan_agent._DIRECT_PLAN_PAYLOAD_PREFIX + plan

        mock_import.return_value = (
            InvalidDirectMarkdown,
            MockLiteLLMModel,
            object,
        )
        with pytest.raises(AgentTaskError, match=message) as caught:
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                require_direct_submission=True,
                direct_submission_protocol=plan_agent.DIRECT_MARKDOWN_PROTOCOL,
            )

        assert caught.value.reason == "plan_invalid_markdown"
        assert caught.value.trajectory[-1]["content"] == "FINAL_PLAN\n" + plan

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_submitted_plan_cannot_be_overridden_by_tmp_file(
        self, mock_import, config
    ):
        mock_import.return_value = (
            MockDefaultAgentDirect,
            MockLiteLLMModel,
            object,
        )

        class Environment:
            def execute(self, _command):
                return {
                    "returncode": 0,
                    "stdout": "corrupted temporary Plan",
                    "stderr": "",
                }

        plan, _ = plan_agent.run(
            config,
            "Fix parser bug",
            Environment(),
            require_direct_submission=True,
        )
        assert plan.startswith("# Plan\n")

    def test_plan_artifact_uses_stdout_not_apptainer_stderr(self):
        class Environment:
            def execute(self, _command):
                return {
                    "returncode": 0,
                    "stdout": '{"revised_plan":"safe"}',
                    "stderr": "WARNING: unrelated apptainer diagnostic",
                    "output": (
                        '{"revised_plan":"safe"}'
                        "WARNING: unrelated apptainer diagnostic"
                    ),
                }

        assert plan_agent._read_plan_from_file(Environment()) == (
            '{"revised_plan":"safe"}'
        )

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_empty_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(config, "Fix parser bug", mock_env)

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_empty_plan_persists_failure_trajectory(
        self, mock_import, config, mock_env, tmp_path
    ):
        mock_import.return_value = (MockDefaultAgentEmpty, MockLiteLLMModel, object)
        path = tmp_path / "failed_plan_trajectory.json"
        with pytest.raises(TaskError, match="empty"):
            plan_agent.run(
                config,
                "Fix parser bug",
                mock_env,
                failure_trajectory_path=path,
            )

        record = json.loads(path.read_text())
        assert record["exit_status"] == "Submitted"
        assert record["messages"][-1]["role"] == "assistant"

    @patch("src.agents.plan_agent.import_minisweagent")
    def test_whitespace_only_plan_raises_task_error(self, mock_import, config, mock_env):
        mock_import.return_value = (MockDefaultAgentWhitespace, MockLiteLLMModel, object)
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
