"""No model/container calls: caller policy reaches the shared Native sandbox."""
from dataclasses import replace
from pathlib import Path
import tomllib
from types import SimpleNamespace

import pytest
import yaml

from src.optimization import codex_cli_runtime as runtime
from src.optimization.safe_pce_execution import resolved_pce_payload
from src.swe_verified_pce.config import load_swe_verified_pce_config
from src.swe_verified_pce.hpc_executor import pce_semantic_sha256


@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("disabled", [None, False, True])
def test_preflight_and_inference_share_network_and_filesystem_policy(tmp_path, writable, disabled):
    options = runtime._permission_options(tmp_path, writable, disabled)
    command = runtime._codex_command({"executor": "codex_cli", "model": "test"},
        working_directory=tmp_path, output_path=tmp_path / "final", repository_writable=writable,
        tool_network_disabled=disabled)
    if not writable and disabled is None:
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert options == []
        return
    assert "--sandbox" not in command  # Do not override named profiles with legacy settings.
    for value in options:
        assert value in command
    policy = tomllib.loads("\n".join(options[1::2]))
    name = runtime._permission_profile(writable, disabled)
    assert policy["default_permissions"] == name
    definition = policy["permissions"][name]
    assert definition["network"]["enabled"] == (not disabled)
    if writable:
        assert definition["filesystem"][":workspace_roots"][".git"] == "write"
    else:
        assert definition["extends"] == ":read-only" and "filesystem" not in definition
    if disabled:
        assert 'web_search="disabled"' in command
    if writable or disabled:
        assert "features.apps=false" in command
        assert "apps._default.enabled=false" in command


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("writable", [False, True])
def test_isolated_native_roles_disable_service_tools_without_disabling_delegation(tmp_path, persistent, writable):
    command = runtime._codex_command({"executor": "codex_cli", "model": "gpt-6-sol"},
        working_directory=tmp_path, output_path=tmp_path / "final", repository_writable=writable,
        tool_network_disabled=True, persistent=persistent,
        session_id="same-private-session" if persistent else None)
    options = [command[i + 1] for i, value in enumerate(command) if value == "--config"]
    policy = tomllib.loads("\n".join(options))
    assert policy["features"]["apps"] is False
    assert policy["apps"]["_default"]["enabled"] is False
    assert policy["web_search"] == "disabled"
    assert policy["features"].get("multi_agent", False) is persistent
    assert "mcp_servers" not in policy  # No provider credential or server configuration introduced.


@pytest.mark.parametrize("writable", [False, True])
@pytest.mark.parametrize("result", ["denied", "refused", "connected", "nonzero"])
def test_network_preflight_requires_permission_denial_not_unreachable_endpoint(tmp_path, monkeypatch, writable, result):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1\n", stderr="")
        if "TOOL_NETWORK_DENIED" in command[-1]:
            assert kwargs["timeout"] == 30
            assert "127.0.0.1" in command[-1] and "EPERM" in command[-1]
            assert "--net" not in command
            return SimpleNamespace(returncode=1 if result == "nonzero" else 0,
                stdout="TOOL_NETWORK_DENIED\n" if result == "denied" else result, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    kwargs = dict(working_directory=tmp_path, environment={}, repository_writable=writable,
                  tool_network_disabled=True)
    if result == "denied":
        checks = runtime._runtime_preflight({"codex_version": "0.160.1"}, **kwargs)
        assert checks[-1]["check"] == "tool_network_disabled"
    else:
        with pytest.raises(runtime.CodexConfigurationError) as caught:
            runtime._runtime_preflight({"codex_version": "0.160.1"}, **kwargs)
        assert caught.value.retry_disposition == "block_run"
    assert len(calls) == 3
    for call in calls[1:]:
        for option in runtime._permission_options(tmp_path, writable, True):
            assert option in call


@pytest.mark.parametrize("denial_verified", [False, True])
def test_network_preflight_gates_real_transport_wiring(tmp_path, monkeypatch, denial_verified):
    from tests.test_optimization.test_codex_sif_execution import _inputs
    sif, evidence, repo, model = _inputs(tmp_path)
    model["codex_version"] = "0.160.1"
    boundary = runtime.CodexSIFExecution(sif, evidence, repo, repository_writable=True,
        tool_network_disabled=True, output_files=("codex_final_response.txt",))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert "--net" not in command and "--network" not in command  # CLI networking survives.
        if "sif-python-probe" in command:
            return SimpleNamespace(returncode=0, stdout=command[-2] + "\n", stderr="")
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1\n", stderr="")
        if "TOOL_NETWORK_DENIED" in command[-1]:
            return SimpleNamespace(returncode=0, stdout="TOOL_NETWORK_DENIED" if denial_verified else "", stderr="")
        if command[-1] == "-":
            assert any("network={enabled=false}" in part for part in command)
            output = next(Path(command[i+1].split(":")[0]) for i, value in enumerate(command)
                          if value == "--bind" and ":/agent-output:" in command[i+1])
            (output / "codex_final_response.txt").write_text("Original final text")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    kwargs = dict(container=boundary, model_config=model, working_directory=repo,
                  attempt_dir=tmp_path / "attempt", system="Plan", user="Task", task="Plan")
    if denial_verified:
        raw, trace = runtime.run_codex_text_agent(**kwargs)
        assert raw == "Original final text"
        assert next(m["content"] for m in trace if m["role"] == "codex_cli")["tool_network_disabled"] is True
    else:
        with pytest.raises(runtime.CodexConfigurationError, match="network isolation"):
            runtime.run_codex_text_agent(**kwargs)
    assert any(command[-1] == "-" for command in calls) == denial_verified


def test_verified_network_flag_survives_config_materialization_and_fingerprint(tmp_path):
    source = Path("configs/safe_pce_ace_verified_codex_smoke_source_v3_20261023.yaml")
    original = load_swe_verified_pce_config(source, require_api_keys=False)
    raw = resolved_pce_payload(original)
    raw["container"]["network_disabled"] = True
    path = tmp_path / "pce.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_swe_verified_pce_config(path, require_api_keys=False)
    assert config.agent_network_disabled is True
    assert resolved_pce_payload(config)["container"]["network_disabled"] is True
    assert original.agent_network_disabled is False
    assert pce_semantic_sha256(config) != pce_semantic_sha256(replace(config, agent_network_disabled=False))
    assert config.slurm_evaluator_timeout_outcome == original.slurm_evaluator_timeout_outcome


def test_verified_miniswe_environment_receives_same_network_flag(tmp_path, monkeypatch):
    from src.swe_verified_pce import runner
    source = Path("configs/safe_pce_ace_verified_codex_smoke_source_v3_20261023.yaml")
    config = replace(load_swe_verified_pce_config(source, require_api_keys=False), agent_network_disabled=True)
    captured = {}
    def environment(**kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(runner, "ApptainerEnvironment", environment)
    instance = runner.SWEVerifiedPCERunner.__new__(runner.SWEVerifiedPCERunner)
    instance.config, instance.capacity_window, instance.source_access_path = config, object(), tmp_path / "source.jsonl"
    instance._environment(SimpleNamespace(image=SimpleNamespace(requested_ref="synthetic"),
        issue_description="Task", instance_id="case"), timeout=17, phase="plan")
    assert captured["network_disabled"] is True and captured["timeout"] == 17


def test_native_pce_adapter_forwards_network_flag_without_replacing_runner(tmp_path, monkeypatch):
    from src.agents.pce_backend import agent_runner
    from src.config import Config
    from src.optimization.audit import JsonlLogger
    from src.agents import pce_backend
    captured = {}
    def native(*args, **kwargs):
        captured.update(kwargs)
        return "unchanged"
    monkeypatch.setattr(pce_backend, "_run_plan", native)
    config = SimpleNamespace(plan_submission_protocol="direct_human_markdown_v5", agent_network_disabled=True,
        agent_backends={"plan": {"executor": "codex_cli"}}, docker=SimpleNamespace(workdir="/testbed"))
    invoke = agent_runner(None, role="plan", config=config,
        case=SimpleNamespace(instance_id="case", image=SimpleNamespace(sif_path=tmp_path / "case.sif")),
        workspace=tmp_path, attempt_dir=tmp_path, source_access_path=tmp_path / "source.jsonl",
        usage=JsonlLogger(tmp_path / "usage.jsonl"))
    assert invoke(Config(), "Task", object()) == "unchanged"
    assert captured["tool_network_disabled"] is True


def test_both_native_pce_roles_build_the_declared_network_boundary(tmp_path, monkeypatch):
    from src.agents import pce_backend
    from tests.test_agents.test_pce_codex_backend import _completion, _context, _config, _diff_result, PATCH, PLAN
    calls = _completion(monkeypatch)
    context = dict(_context(tmp_path), tool_network_disabled=True)
    pce_backend._run_plan(_config(), "Task", None, **context)
    pce_backend._run_code(_config(), PLAN, "Task",
        SimpleNamespace(execute=lambda command: _diff_result(PATCH)), **context)
    assert len(calls) == 2 and all(c["container"].tool_network_disabled is True for c in calls)


def test_v4_smoke_preserves_budget_models_resources_and_uses_fresh_identity():
    from src.optimization.config import load_optimization_config
    old_path = Path("configs/gepa_safe_pce_verified_codex_smoke_v3_20261023.yaml")
    new_path = Path("configs/gepa_safe_pce_verified_codex_smoke_v4_20261023.yaml")
    old = load_optimization_config(old_path, require_api_keys=False)
    new = load_optimization_config(new_path, require_api_keys=False)
    raw = yaml.safe_load(new_path.read_text())
    assert old.search == new.search
    assert new.run_dir != old.run_dir and new.dataset_snapshot != old.dataset_snapshot
    assert raw["experiment_contract"]["launch_authorized"] is False
    assert raw["experiment_contract"]["previous_output_import"] is False
    for role in ("reflector_model", "curator_model", "refiner_model"):
        assert getattr(new.hybrid, role) == getattr(old.hybrid, role)
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts"):
        assert getattr(new.hpc, field) == getattr(old.hpc, field)
    previous = load_swe_verified_pce_config("configs/safe_pce_ace_verified_codex_smoke_source_v3_20261023.yaml", require_api_keys=False)
    current = load_swe_verified_pce_config("configs/safe_pce_ace_verified_codex_smoke_source_v4_20261023.yaml", require_api_keys=False)
    for field in ("plan", "code", "agent_backends", "docker", "container", "instance_ids", "image_manifest",
                  "plan_submission_protocol", "slurm_evaluator_timeout_outcome", "code_prompt", "code_instance_template"):
        assert getattr(previous, field) == getattr(current, field)
    assert current.agent_network_disabled is True and previous.agent_network_disabled is False
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts", "max_running_array_tasks"):
        assert getattr(previous.hpc, field) == getattr(current.hpc, field)
    supervisor = yaml.safe_load(Path("configs/archive/supervisor_launches/safe_pce_ace_verified_codex_smoke_supervisor_v4_20261023.yaml").read_text())
    args = supervisor["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(new_path)
    assert "--require-clean-worktree" in args
    assert raw["hpc"]["worker_config_path"] == str(new_path)
    assert new_path.stem in raw["hpc"]["remote_project_dir"]
