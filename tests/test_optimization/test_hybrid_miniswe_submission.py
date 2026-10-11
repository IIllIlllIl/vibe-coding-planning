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
        self.inputs = []

    def query(self, messages):
        self.inputs.append([dict(message) for message in messages])
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


@pytest.mark.parametrize("bad", [
    "", "[]",
    '{"rule_results":[{"rule_number":1,"triggered":false,"finding":"Not empty","evidence":[]}]}',
])
def test_real_agent_rejects_raw_output_then_accepts_agent_correction(tmp_path, bad):
    candidate = json.loads(Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text())
    good = '{"rule_results":[{"rule_number":1,"triggered":false,"finding":null,"evidence":[]}]}'
    observed = []
    def validate(payload):
        observed.append(payload)
        parse_hybrid_checker_output(_checker_submission_object("Submitted", payload), candidate)
    def action(payload):
        return "```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT '" + payload + "'\n```"
    model = RecordedModel([action(bad), action(good)])
    agent = build_default_agent(DefaultAgent, model, LocalEnvironment(cwd=str(tmp_path)),
        system_template="Submit the required JSON object.", instance_template="{{task}}", step_limit=3,
        submission_validator=validate)
    status, submission = agent.run(task="Check")
    assert status == "Submitted"
    assert json.loads(submission) == json.loads(good)
    assert observed == [bad + "\n", good + "\n"]
    assert model.n_calls == 2
    assert any("Host rejected the submission" in m["content"] for m in agent.messages)
    feedback = [m["content"] for m in model.inputs[1]
                if "Host rejected the submission" in m["content"]]
    assert len(feedback) == 1
    expected = "empty payload" if bad == "" else (
        "JSON object" if bad == "[]" else "null/empty fields")
    assert expected in feedback[0]
    assert "Earlier observations remain available" in feedback[0]


def test_marker_before_redirect_reproduces_smoke_and_feedback_reaches_next_query(tmp_path):
    payload = '{"rule_results":[{"rule_number":1,"triggered":false,"finding":null,"evidence":[]}]}'
    path = tmp_path / "final_output"
    first = ("```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && "
             f"cat > '{path}' <<'EOF'\n{payload}\nEOF\n```")
    second = ("```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && "
              f"cat '{path}'\n```")
    seen = []
    def validate(submission):
        seen.append(submission)
        _checker_submission_object("Submitted", submission)
    model = RecordedModel([first, second])
    agent = build_default_agent(DefaultAgent, model, LocalEnvironment(cwd=str(tmp_path)),
        system_template="Submit JSON", instance_template="{{task}}", step_limit=3,
        submission_validator=validate)
    status, submission = agent.run(task="Check")
    assert seen == ["", payload + "\n"]
    assert status == "Submitted"
    assert submission == payload + "\n"
    assert path.read_text() == payload + "\n"
    assert any("empty payload" in message["content"] for message in model.inputs[1])


def test_invalid_submission_recovery_retains_original_step_budget(tmp_path):
    def reject(payload):
        raise ValueError("expected an object")
    model = RecordedModel(["```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT '[]'\n```"])
    agent = build_default_agent(DefaultAgent, model, LocalEnvironment(cwd=str(tmp_path)),
        system_template="Check", instance_template="{{task}}", step_limit=1,
        submission_validator=reject)
    status, submission = agent.run(task="Check")
    assert (status, submission) == ("LimitsExceeded", "")
    assert model.n_calls == 1
