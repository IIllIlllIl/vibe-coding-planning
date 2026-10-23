"""Same-loop correction using real mini-SWE and the real Native hook adapter."""
from copy import deepcopy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from src.agents import code_agent, plan_agent, pce_backend
from src.agents.codex_hooks import stop_result, validate_native_submission
from src.config import Config, PromptConfig
from src.optimization.audit import JsonlLogger

PLAN = "START_PLAN\n# Plan\nInspect {{data}} and implement the requested change.\nEND_PLAN\n"
BAD = "START_PLAN\n# Plan\nEND_PLAN"
PATCH = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.n_calls = 0
        self.cost = 0
        self.inputs = []

    def query(self, messages):
        self.inputs.append(deepcopy(messages))
        self.n_calls += 1
        return {"content": next(self.responses)}

    def get_template_vars(self):
        return {}


def config():
    return Config(prompts=PromptConfig(plan_generation_prompt="Plan {{task}}", plan_instance_template="{{task}}",
        code_generation_prompt="Implement {{plan}}", code_instance_template="{{task}}"))


def test_real_miniswe_plan_corrects_same_session_with_identical_stop_feedback(tmp_path, monkeypatch):
    from minisweagent.agents.default import DefaultAgent
    model = ScriptedModel([BAD, PLAN])
    monkeypatch.setattr(plan_agent, "import_minisweagent", lambda: (DefaultAgent, object, object))
    monkeypatch.setattr(plan_agent, "build_model", lambda *a, **kw: model)
    env = SimpleNamespace(get_template_vars=lambda: {})
    result, trace = plan_agent.run(config(), "Task", env, require_direct_submission=True,
        direct_submission_protocol="direct_human_markdown_v5")
    native = stop_result({"hook_event_name": "Stop", "last_assistant_message": BAD},
        {"submission_contract": {"phase": "plan"}}, tmp_path / "stop.jsonl")
    assert model.n_calls == 2
    assert model.inputs[1][-1]["content"] == native["reason"]
    assert result == PLAN[len("START_PLAN\n"):-len("END_PLAN\n")]
    assert any(m["role"] == "assistant" and m["content"] == BAD for m in trace)
    assert any(m["role"] == "assistant" and m["content"] == PLAN for m in trace)


def test_real_miniswe_coder_corrects_empty_submission_in_same_session(monkeypatch):
    from minisweagent.agents.default import DefaultAgent
    model = ScriptedModel(["```bash\nsubmit_empty\n```", "```bash\nsubmit_patch\n```"])
    observed = iter(["COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n" + PATCH])
    monkeypatch.setattr(code_agent, "import_minisweagent", lambda: (DefaultAgent, object, object))
    monkeypatch.setattr(code_agent, "build_model", lambda *a, **kw: model)
    env = SimpleNamespace(get_template_vars=lambda: {}, execute=lambda command: {"output": next(observed), "returncode": 0})
    result, trace = code_agent.run(config(), "Plan", "Task", env, validate_submission_in_loop=True)
    assert result == PATCH and model.n_calls == 2
    assert "the submitted patch is empty" in model.inputs[1][-1]["content"]
    assert "Host observed submission stdout" in json.dumps(trace)


def test_real_miniswe_plan_corrects_legacy_stdout_submission(monkeypatch):
    from minisweagent.agents.default import DefaultAgent
    model = ScriptedModel(["```bash\nlegacy_submit\n```", PLAN])
    monkeypatch.setattr(plan_agent, "import_minisweagent", lambda: (DefaultAgent, object, object))
    monkeypatch.setattr(plan_agent, "build_model", lambda *a, **kw: model)
    raw_stdout = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n# Plan\nOriginal stdout Plan\n"
    env = SimpleNamespace(get_template_vars=lambda: {}, execute=lambda command: {"output": raw_stdout, "returncode": 0})
    result, trace = plan_agent.run(config(), "Task", env, require_direct_submission=True,
        direct_submission_protocol="direct_human_markdown_v5")
    assert model.n_calls == 2 and "direct START_PLAN terminal response" in model.inputs[1][-1]["content"]
    assert any(m["content"] == "Host observed submission stdout:\n" + raw_stdout for m in trace)
    assert "Inspect {{data}}" in result


def test_native_plan_stop_retains_bad_submission_and_requires_accepted_final(tmp_path):
    context = {"submission_contract": {"phase": "plan"}}
    audit = tmp_path / "stop.jsonl"
    assert stop_result({"hook_event_name": "Stop", "last_assistant_message": BAD}, context, audit)["decision"] == "block"
    with pytest.raises(ValueError):
        validate_native_submission(BAD, audit, contract=context["submission_contract"], output_dir=tmp_path)
    assert stop_result({"hook_event_name": "Stop", "last_assistant_message": PLAN}, context, audit) == {}
    records = validate_native_submission(PLAN, audit, contract=context["submission_contract"], output_dir=tmp_path)
    assert [r["raw_submission"] for r in records] == [BAD, PLAN]
    with pytest.raises(ValueError, match="matching accepted"):
        validate_native_submission(PLAN + "changed", audit, contract=context["submission_contract"], output_dir=tmp_path)


def test_native_coder_stop_checks_real_staging_and_never_repairs_patch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "file.py").write_text("new content\n")
    subprocess.run(["git", "-C", str(repo), "add", "file.py"], check=True)
    marker = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    context = {"submission_contract": {"phase": "code", "workdir": str(repo), "allow_empty": False}}
    audit = tmp_path / "stop.jsonl"
    event = {"hook_event_name": "Stop", "last_assistant_message": marker}
    assert "regular file" in stop_result(event, context, audit, output_dir=tmp_path)["reason"]
    patch = tmp_path / "submission.patch"
    patch.write_bytes(b"wrong patch")
    assert "differs" in stop_result(event, context, audit, output_dir=tmp_path)["reason"]
    assert patch.read_bytes() == b"wrong patch"
    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--binary", "--full-index"], capture_output=True, check=True).stdout
    patch.write_bytes(staged)
    assert stop_result(event, context, audit, output_dir=tmp_path) == {}
    records = validate_native_submission(marker, audit, contract=context["submission_contract"], output_dir=tmp_path)
    assert records[-1]["patch_sha256"] == hashlib.sha256(staged).hexdigest()
    patch.write_bytes(staged + b"changed")
    with pytest.raises(ValueError, match="patch changed"):
        validate_native_submission(marker, audit, contract=context["submission_contract"], output_dir=tmp_path)


@pytest.mark.parametrize("role", ["plan", "code"])
@pytest.mark.parametrize("native", [False, True])
def test_safe_pce_agents_have_no_host_limits_and_keep_original_config(tmp_path, monkeypatch, role, native):
    original = config()
    cfg = SimpleNamespace(plan_submission_protocol="direct_human_markdown_v5", agent_backends={})
    if native:
        cfg.agent_backends[role] = {"executor": "codex_cli"}
    calls = []
    shell_calls = []
    env = SimpleNamespace(execute=lambda *a, **kw: shell_calls.append(kw) or {"returncode": 0})
    def agent(*args, **kwargs):
        calls.append((args, kwargs))
        if not native:
            args[-1].execute("git status")
        return "result"
    monkeypatch.setattr(pce_backend, "_run_" + role, agent)
    runner = pce_backend.agent_runner(agent, role=role, config=cfg,
        case=SimpleNamespace(image=SimpleNamespace(sif_path=tmp_path / "case.sif"), instance_id="case"),
        workspace=tmp_path, attempt_dir=tmp_path, source_access_path=tmp_path / "source.jsonl",
        usage=JsonlLogger(tmp_path / "usage.jsonl"))
    cfg.docker = SimpleNamespace(workdir="/testbed")
    args = (original, "task", env) if role == "plan" else (original, "plan", "task", env)
    assert runner(*args, **({"phase_timeout_seconds": 1} if role == "code" else {})) == "result"
    effective = calls[0][0][0].agent
    assert (effective.max_steps, effective.cost_limit, effective.timeout) == (0, 0, 0)
    assert original.agent.max_steps == 30 and original.agent.cost_limit == 3
    if role == "code":
        assert calls[0][1]["phase_timeout_seconds"] is None
    if not native:
        assert shell_calls == [{"timeout": 0}]


def test_zero_agent_command_timeout_does_not_disable_host_timeout(tmp_path, monkeypatch):
    from tests.test_environment.test_apptainer_env import _TrackingCapacityWindow
    from src.environment.apptainer_env import ApptainerEnvironment
    values = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: values.append(kw.get("timeout")) or
        subprocess.CompletedProcess(args, 0, stdout="ok", stderr=""))
    cache = tmp_path / "sifs"
    cache.mkdir()
    (cache / "python_3.12-slim.sif").write_text("synthetic image")
    env = ApptainerEnvironment(image="python:3.12-slim", cwd="/testbed", sif_cache_dir=cache,
        capacity_window=_TrackingCapacityWindow(), timeout=17)
    values.clear()
    pce_backend._AgentEnvironment(env).execute("git status")
    env.execute("git status")
    assert values == [None, 17] and env._timeout == 17
