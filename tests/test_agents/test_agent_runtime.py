"""Cross-workflow dispatch and permissions, with no inference or containers."""
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from src.agents.runtime import AgentConfigurationError, credential_envs, run_agent
from src.agents import pce_backend
from src.optimization import codex_cli_runtime as codex, playbook_runtime
from src.swe_verified_pce.worker import _retry_disposition


@pytest.mark.parametrize("executor", ["mini_swe", "codex_cli"])
def test_one_call_exact_result_and_only_selected_backend(executor):
    result = (object(), [{"raw": "{{unchanged}}\n\n"}])
    calls = []
    def selected():
        calls.append(executor)
        return result
    def forbidden():
        pytest.fail("unselected backend initialized")
    handlers = {executor: selected, "codex_cli" if executor == "mini_swe" else "mini_swe": forbidden}
    assert run_agent({"executor": executor}, **handlers) is result
    assert calls == [executor]


@pytest.mark.parametrize("config", [{"executor": "typo"}, {"executor": "codex_cli"}])
def test_invalid_or_unavailable_backend_blocks_without_fallback(config):
    with pytest.raises(AgentConfigurationError) as caught:
        run_agent(config, mini_swe=lambda: pytest.fail("must not fall back"))
    assert _retry_disposition(caught.value, stage="agent_execution") == "block_run"


def test_error_identity_and_raw_trajectory_survive_without_internal_retry():
    error = RuntimeError("original failure")
    error.trajectory = [{"content": "original raw data"}]
    calls = []
    def fail():
        calls.append(1)
        raise error
    with pytest.raises(RuntimeError) as caught:
        run_agent({}, mini_swe=fail)
    assert caught.value is error and calls == [1]
    assert caught.value.trajectory is error.trajectory


@pytest.mark.parametrize("executor", ["mini_swe", "codex_cli"])
def test_pce_and_gepa_use_same_entry_and_preserve_role_contracts(tmp_path, monkeypatch, executor):
    assert pce_backend.run_agent is playbook_runtime.run_agent is run_agent
    result = ("exact role artifact\n", [{"native": "opaque"}])
    seen = []
    def record(*args, **kwargs):
        seen.append(kwargs)
        return result
    monkeypatch.setattr(pce_backend, "_run_plan", record)
    monkeypatch.setattr(playbook_runtime, "_run_evidence_json_agent", record)
    monkeypatch.setattr(codex, "run_codex_json_agent", record)
    monkeypatch.setattr(playbook_runtime, "_codex_evidence_sif", lambda *_: tmp_path / "image.sif")
    model = {"executor": executor}
    config = SimpleNamespace(agent_backends={"plan": model}, docker=SimpleNamespace(workdir="/testbed"))
    case = SimpleNamespace(image=SimpleNamespace(sif_path=str(tmp_path / "image.sif")), instance_id="case")
    plan = pce_backend.agent_runner(record, role="plan", config=config, case=case,
        workspace=tmp_path, attempt_dir=tmp_path, source_access_path=None, usage=None)
    assert plan("input", planning_rules="same rules") is result
    assert playbook_runtime.run_evidence_reflector(model_config=model, reflection_config={},
        system="Same objective", instance_template="{{internal_playbook}}", evidence_dir=str(tmp_path),
        internal_playbook="same rules") is result
    assert len(seen) == 2


def test_credentials_follow_mixed_backends_and_reject_shell_injection():
    assert credential_envs([{"executor": "codex_cli"}]) == ()
    assert credential_envs([{}, {"executor": "codex_cli"}, {"api_key_env": "OTHER_KEY"}]) == (
        "DEEPSEEK_API_KEY", "OTHER_KEY")
    with pytest.raises(AgentConfigurationError):
        credential_envs([{"api_key_env": "KEY; echo unsafe"}])


def test_preflight_and_inference_share_parseable_writable_policy():
    cwd = Path("/testbed")
    options = codex._workspace_options(cwd)
    policy = tomllib.loads("\n".join(options[1::2]))
    name = policy["default_permissions"]
    assert name == codex._permission_profile(True)
    definition = policy["permissions"][name]
    assert definition["extends"] == ":workspace"
    assert definition["workspace_roots"] == {"/testbed": True}
    assert definition["filesystem"] == {
        ":workspace_roots": {".": "write", ".git": "write"}, "/agent-output": "write"}
    command = codex._codex_command({"executor": "codex_cli", "model": "test"},
        working_directory=cwd, output_path=Path("/agent-output/final.json"), repository_writable=True)
    for option in options[1::2]:
        assert option in command
    assert "--sandbox" not in command
    assert not any("sandbox_workspace_write" in word or ":workspace-write" in word for word in command)


def test_unknown_permission_is_permanent_and_never_reaches_model(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="",
            stderr='default_permissions refers to unknown built-in profile `:workspace-write`')
    monkeypatch.setattr(codex.subprocess, "run", run)
    with pytest.raises(codex.CodexConfigurationError) as caught:
        codex._runtime_preflight({"codex_version": "0.160.1"}, working_directory=tmp_path,
                                  environment={}, repository_writable=True)
    assert _retry_disposition(caught.value, stage="agent_execution") == "block_run"
    assert len(calls) == 2 and "sandbox" in calls[-1]
    assert caught.value.trajectory[0]["content"]["checks"][-1]["returncode"] == 1


def test_generic_codex_output_contract_does_not_force_read_only(tmp_path, monkeypatch):
    raw = ('{"unchanged": true}\n', [{"raw": "event"}])
    monkeypatch.setattr(codex, "_run_codex_text_agent", lambda **kwargs: raw)
    boundary = codex.CodexSIFExecution(tmp_path / "image", tmp_path,
        repository_dir=tmp_path, repository_writable=True, output_files=("result.json",))
    assert codex.run_codex_agent(container=boundary, output_filename="result.json") is raw
    with pytest.raises(ValueError, match="read-only"):
        codex.run_codex_json_agent(container=boundary)
