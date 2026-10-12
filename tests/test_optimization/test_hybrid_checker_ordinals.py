import copy
import json

import pytest
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment

from src.agents._deps import build_default_agent
from src.optimization.behavioral_hybrid import checker_projection, parse_hybrid_checker_output
from tests.test_optimization.test_hybrid_miniswe_submission import RecordedModel


def artifact():
    return {"schema_version": 1, "shared_guide": "Reject any triggered concern.",
            "checklist": [{"id": f"plan-{i:05d}", "condition": f"Concern {i}",
                           "guide": f"Investigate concern {i}"} for i in range(2, 8)]}


def output(numbers):
    return {"rule_results": [{"rule_number": i, "triggered": False,
                              "finding": None, "evidence": []} for i in numbers]}


def test_projection_keeps_science_and_hides_noncontiguous_ids():
    parent = artifact()
    before = copy.deepcopy(parent)
    visible = checker_projection(parent)
    assert parent == before
    assert visible["shared_guide"] == parent["shared_guide"]
    assert [row["rule_number"] for row in visible["checklist"]] == list(range(1, 7))
    for source, row in zip(parent["checklist"], visible["checklist"], strict=True):
        assert "id" not in row
        assert row["condition"] == source["condition"]
        assert row["guide"] == source["guide"]


@pytest.mark.parametrize("numbers,expected", [
    (range(1, 8), "expected 6 results, received 7"),
    (range(2, 8), "result[0].rule_number: expected 1, received 2"),
])
def test_feedback_identifies_exact_count_or_position_without_repair(numbers, expected):
    raw = output(numbers)
    before = copy.deepcopy(raw)
    with pytest.raises(ValueError) as error:
        parse_hybrid_checker_output(raw, artifact())
    assert expected in str(error.value)
    assert raw == before


def test_real_submission_loop_recovers_from_observed_seven_for_six_failure(tmp_path):
    bad, good = json.dumps(output(range(1, 8))), json.dumps(output(range(1, 7)))
    def action(payload):
        return "```bash\nprintf '%s\\n' COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT '" + payload + "'\n```"
    observed = []
    def validate(raw):
        observed.append(raw)
        parse_hybrid_checker_output(json.loads(raw), artifact())
    model = RecordedModel([action(bad), action(good)])
    agent = build_default_agent(DefaultAgent, model, LocalEnvironment(cwd=str(tmp_path)),
        system_template="Check", instance_template="{{task}}", step_limit=3,
        submission_validator=validate)
    status, submission = agent.run(task="Check")
    assert status == "Submitted"
    assert json.loads(submission) == json.loads(good)
    assert observed == [bad + "\n", good + "\n"]
    feedback = "\n".join(row["content"] for row in model.inputs[1] if row["role"] == "user")
    assert "expected 6 results, received 7" in feedback
    assert "[1, 2, 3, 4, 5, 6]" in feedback
    assert "Host has not renumbered" in feedback


@pytest.mark.parametrize("executor", ["mini_swe", "codex_cli"])
def test_production_checker_inputs_use_same_projection(tmp_path, monkeypatch, executor):
    from contextlib import contextmanager
    from dataclasses import replace
    from types import SimpleNamespace
    import src.optimization.behavioral_runtime as runtime
    import src.optimization.behavioral_hybrid_runtime as phases
    import src.optimization.codex_cli_runtime as codex
    from src.optimization.config import load_optimization_config
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_formal3_v5_20261011.yaml",
                                      require_api_keys=False)
    config = replace(config, run_dir=tmp_path,
        hybrid=replace(config.hybrid, checker_model={"executor": "codex_cli"} if executor == "codex_cli" else {}))
    case = SimpleNamespace(instance_id="case", pre_p1_context=[], proposed_plan_p1="Plan",
        repository=SimpleNamespace(repo="repo", proxy_commit="base"),
        audit_provenance={"mirror_relpath": "repo"}, checker_payload=lambda: {"proposed_plan_p1": "Plan"})
    seen = []
    raw = output(range(1, 7))
    @contextmanager
    def repository(*args, **kwargs):
        yield tmp_path
    if executor == "codex_cli":
        monkeypatch.setattr(phases, "require_evidence_sif", lambda *_: tmp_path / "image.sif")
        monkeypatch.setattr(phases, "require_history_bundle", lambda *_: (tmp_path / "history.bundle", {}))
        monkeypatch.setattr(runtime, "materialize_repository_proxy", repository)
        def run_codex(**kwargs):
            evidence = json.loads((kwargs["evidence_manifest_path"].parent / "input.json").read_text())
            seen.append(evidence["checking_artifact"])
            return raw, []
        monkeypatch.setattr(codex, "run_codex_json_agent", run_codex)
    else:
        class Environment:
            def execute(self, *args, **kwargs):
                return {"returncode": 0, "stdout": json.dumps(raw), "stderr": ""}
        @contextmanager
        def mini_repository(*args, **kwargs):
            yield tmp_path, Environment()
        def build(*args, **kwargs):
            class Agent:
                messages = []
                def run(self, **values):
                    seen.append(json.loads(values["candidate_guideline"]))
                    return "Submitted", json.dumps(raw)
            return Agent()
        monkeypatch.setenv(config.checker.api_key_env, "test-only")
        monkeypatch.setattr(runtime, "import_minisweagent", lambda: (object, object, object))
        monkeypatch.setattr(runtime, "build_model", lambda *args, **kwargs: object())
        monkeypatch.setattr(runtime, "build_default_agent", build)
        monkeypatch.setattr(runtime.BehavioralLocalChecker, "_repository_environment", mini_repository)
        monkeypatch.setattr(runtime, "_post_boundary_leakage", lambda *args: [])
    result = runtime.BehavioralLocalChecker(config)(case, json.dumps(artifact()))
    assert result.predicted_accept
    assert seen == [checker_projection(artifact())]
