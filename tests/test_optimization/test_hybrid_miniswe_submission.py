"""Real mini-SWE parser/termination tests, without model API calls."""
import json
from pathlib import Path
import pytest

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment

from src.agents._deps import DEFAULT_ACTION_PROTOCOL, build_default_agent
from src.optimization.behavioral_hybrid import parse_hybrid_checker_output
from src.optimization.config import load_optimization_config
from src.optimization.behavioral_runtime import _checker_submission_object


class RecordedModel:
    cost = 0.0
    n_calls = 0

    def __init__(self, responses):
        self.responses = iter(responses)

    def query(self, messages):
        self.n_calls += 1
        return {"content": next(self.responses)}

    def get_template_vars(self):
        return {}


def test_actual_deepseek_style_agent_submits_exact_json(tmp_path):
    config = load_optimization_config(
        "configs/gepa_swe_chat_hybrid_formal3_v3_20261011.yaml", require_api_keys=False)
    candidate = json.loads(Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text())
    payload = {"rule_results": [{"rule_number": 1, "triggered": False,
                                "finding": None, "evidence": []}]}
    raw = json.dumps(payload)
    response = "```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT '" + raw + "'\n```"
    agent = build_default_agent(DefaultAgent, RecordedModel([response]),
        LocalEnvironment(cwd=str(tmp_path)), system_template=config.checker_prompt,
        instance_template=config.checker_instance_template, step_limit=3)
    status, submission = agent.run(task="Check", pre_p1_context="[]", proposed_plan_p1="A real plan",
        repository_repo="repo", repository_proxy_commit="base", candidate_guideline=json.dumps(candidate),
        retry_feedback="")
    assert status == "Submitted"
    assert json.loads(submission) == payload
    assert parse_hybrid_checker_output(json.loads(submission), candidate).predicted_accept
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in agent.messages[0]["content"]


def test_printing_payload_without_marker_is_not_submission(tmp_path):
    model = RecordedModel(["```bash\necho 'rule_results: []'\n```"])
    agent = build_default_agent(DefaultAgent, model, LocalEnvironment(cwd=str(tmp_path)),
        system_template="Check", instance_template="{{task}}", step_limit=1)
    status, submission = agent.run(task="Check")
    assert status == "LimitsExceeded"
    assert submission == ""
    assert any("rule_results: []" in msg["content"] for msg in agent.messages if msg["role"] == "user")
    assert "printing a payload without it" in DEFAULT_ACTION_PROTOCOL


@pytest.mark.parametrize("status,submission,message", [
    ("LimitsExceeded", "", "exit_status=LimitsExceeded"),
    ("Submitted", "", "empty payload"),
    ("Submitted", "[]", "JSON object"),
])
def test_incomplete_or_invalid_submission_is_not_repaired(status, submission, message):
    with pytest.raises(ValueError, match=message):
        _checker_submission_object(status, submission)
