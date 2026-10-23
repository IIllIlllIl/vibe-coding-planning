"""No model/container calls: native transport, exact artifacts and shared PCE flow."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.agents import pce_backend as backend
from src.config import Config, PromptConfig
from src.exceptions import AgentTaskError
from src.optimization import codex_cli_runtime as runtime
from src.optimization.audit import JsonlLogger


NATIVE = dict(executor="codex_cli", model="gpt-6-sol", reasoning_effort="high",
              codex_binary="/private/codex/bin/codex", codex_auth_file="/private/auth.json",
              codex_version="0.160.1")
PLAN = '# Plan\n\nPreserve {{literal}} and {% data %}.\n\nRule 1: inspect the relevant call sites.\n'
PATCH = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'


def _context(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    attempt = tmp_path / "attempt"
    return dict(backend=NATIVE, sif_path=tmp_path / "case.sif", workspace=workspace,
                attempt_dir=attempt, workdir="/testbed", source_access_path=tmp_path / "source.jsonl",
                usage=JsonlLogger(tmp_path / "usage.jsonl"), instance_id="org__repo-1")


def _config():
    return Config(prompts=PromptConfig(plan_generation_prompt="Plan the task.",
        plan_instance_template="{{task}}\n{{planning_rules}}", code_generation_prompt="Implement {{plan}}",
        code_instance_template="{{task}}"))


def _diff_result(patch):
    # The environment supplies the byte digest, not a newline-normalized diff.
    return {"returncode":0, "stdout":hashlib.sha256(patch.encode()).hexdigest()+"  -\n"}


def _completion(monkeypatch, *, final=None, patch=PATCH, events=None):
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        role = "code" if "Implement" in kwargs["system"] else "plan"
        raw = final if final is not None else (
            "START_PLAN\n" + PLAN + "END_PLAN\nProvider trailer" if role == "plan"
            else "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")
        kwargs["attempt_dir"].mkdir(parents=True, exist_ok=True)
        if patch is not None:
            (kwargs["attempt_dir"] / "submission.patch").write_bytes(patch.encode())
        trajectory = [{"role": "system", "content": kwargs["system"]},
            {"role": "user", "content": kwargs["user"]},
            {"role": "codex_cli", "content": {"events": events or [], "raw_stdout": "native jsonl"}},
            {"role": "assistant", "content": raw}]
        return raw, trajectory
    monkeypatch.setattr(runtime, "run_codex_text_agent", run)
    return calls


def test_default_agent_is_invoked_unchanged_without_native_dependencies():
    expected = object()
    received = []
    def original(*args, **kwargs):
        received.append((args, kwargs))
        return expected
    invoke = backend.agent_runner(original, role="plan", config=SimpleNamespace(), case=None,
        workspace=None, attempt_dir=None, source_access_path=None, usage=None)
    assert invoke("task", extra="unchanged") is expected
    assert received == [(("task",), {"extra": "unchanged"})]


def test_backends_default_mixed_and_explicit_pin():
    assert backend.load_agent_backends({"plan": {}, "code": {}}, plan_protocol="legacy_stdout_v1") == {}
    assert backend.load_agent_backends({"plan": NATIVE, "code": {}},
        plan_protocol="direct_human_markdown_v5") == {"plan": NATIVE}


@pytest.mark.parametrize("change", [{"executor": "typo"}, {"codex_version": ""},
    {"codex_binary": ""}, {"reasoning_effort": "typo"}, {"api_key": "must-not-stage"}])
def test_invalid_backend_fails_before_inference(change):
    with pytest.raises(ValueError):
        backend.load_agent_backends({"plan": dict(NATIVE, **change), "code": {}},
                                    plan_protocol="direct_human_markdown_v5")


def test_codex_cannot_select_legacy_unsafe_plan_boundary():
    with pytest.raises(ValueError, match="Safe PCE"):
        backend.load_agent_backends({"plan": NATIVE, "code": NATIVE}, plan_protocol="legacy_stdout_v1")


def test_exact_plan_and_template_data_no_reference_projection(tmp_path, monkeypatch):
    calls = _completion(monkeypatch)
    plan, trace = backend._run_plan(_config(), "Issue {{literal}}", None,
                                   planning_rules='{"rule_number":1}', **_context(tmp_path))
    assert plan == PLAN
    assert calls[0]["user"].startswith('Issue {{literal}}\n{"rule_number":1}')
    task = json.loads((tmp_path / "attempt/inputs/task.json").read_text())
    assert task == {"task": "Issue {{literal}}", "planning_rules": '{"rule_number":1}'}
    assert calls[0]["container"].repository_writable
    assert trace[-1]["content"].endswith("Provider trailer")
    assert "native jsonl" in (tmp_path / "attempt/trajectory.json").read_text()


@pytest.mark.parametrize("final", ["# Plan\nUnbounded", "START_PLAN\n# Plan\nEND_PLAN", "START_PLAN\ntext\nEND_PLAN"])
def test_plan_contract_rejection_does_not_repair_text(tmp_path, monkeypatch, final):
    _completion(monkeypatch, final=final)
    with pytest.raises(AgentTaskError) as caught:
        backend._run_plan(_config(), "Issue", None, **_context(tmp_path))
    assert caught.value.trajectory[-1]["content"] == final


@pytest.mark.parametrize("submitted", [PATCH, PATCH.replace("\n", "\r\n")])
def test_raw_patch_not_final_message_or_host_reconstruction(tmp_path, monkeypatch, submitted):
    calls = _completion(monkeypatch, patch=submitted)
    env = SimpleNamespace(execute=lambda command: _diff_result(submitted))
    patch, trace = backend._run_code(_config(), PLAN, "Issue", env, **_context(tmp_path))
    assert patch == submitted
    assert PLAN in calls[0]["system"]
    assert "planning_rules" not in json.loads((tmp_path / "attempt/inputs/task.json").read_text())
    assert trace[-1]["content"] == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
    assert not json.loads((tmp_path / "attempt/submission.json").read_text())["host_patch_transformation"]


@pytest.mark.parametrize("problem", ["missing_patch", "missing_marker", "mismatched_diff", "empty_patch", "symlink"])
def test_code_invalid_submission_never_synthesized(tmp_path, monkeypatch, problem):
    _completion(monkeypatch, patch=None if problem == "missing_patch" else "" if problem == "empty_patch" else PATCH,
                final="Done" if problem == "missing_marker" else None)
    context = _context(tmp_path)
    if problem == "symlink":
        context["attempt_dir"].mkdir()
        (context["attempt_dir"] / "submission.patch").symlink_to(tmp_path / "unrelated.patch")
        (tmp_path / "unrelated.patch").write_text(PATCH)
    env = SimpleNamespace(execute=lambda command: _diff_result("different" if problem == "mismatched_diff" else PATCH))
    with pytest.raises(AgentTaskError):
        backend._run_code(_config(), PLAN, "Issue", env, **context)
    assert not (context["attempt_dir"] / "submission.json").exists()


def test_polybench_explicit_empty_submission_policy_retained(tmp_path, monkeypatch):
    _completion(monkeypatch, patch="")
    env = SimpleNamespace(execute=lambda c: _diff_result(""))
    assert backend._run_code(_config(), PLAN, "Issue", env,
                             allow_empty_submission=True, **_context(tmp_path))[0] == ""


@pytest.mark.parametrize("command", ["git fetch origin", "pip install upstream-package",
    '/bin/bash -lc \'curl https://github.com/org/repo/pull/123.patch\''])
def test_native_source_audit_rejects_before_plan_checkpoint(tmp_path, monkeypatch, command):
    events = [{"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": command}},
              {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": command}}]
    _completion(monkeypatch, events=events)
    context = _context(tmp_path)
    with pytest.raises(AgentTaskError, match="source-access"):
        backend._run_plan(_config(), "Issue", None, **context)
    rows = context["source_access_path"].read_text().splitlines()
    assert len(rows) == 1
    event = json.loads(rows[0])
    assert event["executed"] and event["decision"] == "block"
    assert event["execution_status"] == "native_post_execution_audit"
    assert "command" not in event


@pytest.mark.parametrize("event", [{"type": "turn.failed"}, {"type": "error"},
    {"type": "item.completed", "item": {"type": "web_search"}}])
def test_failure_events_cannot_be_hidden_by_a_final_message(tmp_path, monkeypatch, event):
    _completion(monkeypatch, events=[event])
    with pytest.raises(AgentTaskError):
        backend._run_plan(_config(), "Issue", None, **_context(tmp_path))


def test_native_workspace_transport_reuses_outer_boundary(tmp_path, monkeypatch):
    from tests.test_optimization.test_codex_sif_execution import _inputs
    sif, evidence, repository, model = _inputs(tmp_path)
    model["codex_version"] = "0.160.1"
    boundary = runtime.CodexSIFExecution(sif, evidence, repository, repository_writable=True,
        output_files=("codex_final_response.txt", "submission.patch"))
    seen = []
    def run(command, **kwargs):
        seen.append(command)
        mounts = [command[i+1] for i,v in enumerate(command) if v == "--bind"]
        assert f"{repository}:/testbed:rw" in mounts and f"{evidence}:/evidence:ro" in mounts
        assert "--containall" in command and "timeout" not in kwargs
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1\n", stderr="")
        if "sandbox" in command:
            assert command[command.index("--permission-profile")+1] == "vibe-workspace"
            assert any('".git"="write"' in word for word in command)
        if "exec" in command and command[-1] == "-":
            assert "--sandbox" not in command
            assert 'default_permissions="vibe-workspace"' in command
            output = next(Path(m.split(":")[0]) for m in mounts if ":/agent-output:" in m)
            (output / "codex_final_response.txt").write_bytes(b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n")
            (output / "submission.patch").write_bytes(PATCH.encode())
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}\n', stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)
    attempt = tmp_path / "attempt"
    raw, trace = runtime.run_codex_text_agent(container=boundary, model_config=model,
        working_directory=repository, attempt_dir=attempt, system="System", user="Input", task="Code")
    assert len(seen) == 4
    assert raw == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n"
    assert (attempt / "submission.patch").read_bytes() == PATCH.encode()
    assert trace[3]["content"]["sandbox"] == "workspace-write"
    assert trace[3]["content"]["raw_stdout"] == '{"type":"turn.completed"}\n'
    with pytest.raises(ValueError, match="read-only"):
        runtime.run_codex_json_agent(container=boundary)


def test_backend_options_enter_fingerprint_and_ds_stays_default(tmp_path, monkeypatch):
    from tests.test_optimization.test_safe_pce_learning import _prepared
    from src.optimization.safe_pce_learning import SafePCEBackend
    from src.polybench_pce.hpc_executor import pce_semantic_sha256
    config = _prepared(tmp_path)
    pce = SafePCEBackend(config).pce
    assert pce.agent_backends == {}
    native = replace(pce, agent_backends={"plan": NATIVE, "code": NATIVE})
    assert pce_semantic_sha256(pce) != pce_semantic_sha256(native)
    other = replace(native, agent_backends={"plan": dict(NATIVE, reasoning_effort="low"), "code": NATIVE})
    assert pce_semantic_sha256(other) != pce_semantic_sha256(native)


def test_real_verified_worker_and_runner_native_handoff_and_resume(tmp_path, monkeypatch):
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    from src.swe_verified_pce.dataset import load_swe_verified_pce_cases
    from src.swe_verified_pce.runner import SWEVerifiedPCERunner
    from src.optimization.safe_pce_execution import execute_pce
    from tests.test_swe_verified_pce_pcce import _snapshot
    source, images, _ = _snapshot(tmp_path)
    original = yaml.safe_load(Path("configs/safe_pce_ace_verified_codex_smoke_source_v1_20261023.yaml").read_text())
    original["paths"].update(dataset_snapshot=str(source), image_manifest=str(images),
                             run_dir=str(tmp_path / "run"))
    original["paths"].pop("selection_manifest")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(original))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = load_swe_verified_pce_config(path)
    case = load_swe_verified_pce_cases(source, images)[0][0]
    calls = _completion(monkeypatch)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_verify_sif", lambda *a: None)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_ensure_repository_history",
                        lambda *a: (tmp_path / "history.bundle", {"verified": True}))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_restore_agent_repository", lambda *a, **kw: None)
    def environment(*a, **kw):
        kw["host_workdir"].mkdir(parents=True)
        return SimpleNamespace(cleanup=lambda: None, execute=lambda *a, **kw: _diff_result(PATCH))
    monkeypatch.setattr(SWEVerifiedPCERunner, "_environment", environment)
    monkeypatch.setattr(SWEVerifiedPCERunner, "_capture_code_workspace_evidence", lambda *a, **kw: {})
    seen = []
    def evaluate(patch, target, **kwargs):
        seen.append(patch)
        return {"status":"completed", "task_outcome":"resolved", "evaluator_resolved":True,
                "terminal_kind":"official_tests_resolved"}
    original_run = SWEVerifiedPCERunner.run
    def run(self, case):
        self.evaluator = evaluate
        return original_run(self, case)
    monkeypatch.setattr(SWEVerifiedPCERunner, "run", run)
    monkeypatch.setattr("src.swe_verified_pce.worker.configure_docker_capacity", lambda *a, **kw: SimpleNamespace())
    output = execute_pce(config, "swe_verified_pce", [case], backend="local")[0]
    assert output["plan"] == PLAN and output["patch"] == PATCH and seen == [PATCH]
    assert output["plan_trajectory"][-1]["content"].endswith("Provider trailer")
    assert calls[0]["container"].repository_dir != calls[1]["container"].repository_dir
    assert calls[0]["container"].evidence_dir != calls[1]["container"].evidence_dir
    assert PLAN in calls[1]["system"]
    assert execute_pce(config, "swe_verified_pce", [case], backend="local")[0]["patch"] == PATCH
    assert len(calls) == 2 and seen == [PATCH]


def test_smoke_comparison_changes_only_agent_axis_and_new_identity():
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    from src.optimization.config import load_optimization_config
    from src.optimization.safe_pce_learning import SafePCEBackend
    ds = load_swe_verified_pce_config("configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml", require_api_keys=False)
    native = load_swe_verified_pce_config("configs/safe_pce_ace_verified_codex_smoke_source_v1_20261023.yaml", require_api_keys=False)
    for field in ("dataset_snapshot", "image_manifest", "selection_manifest", "instance_ids", "docker", "container",
                  "slurm_evaluator_timeout_outcome", "plan_submission_protocol", "plan_prompt", "plan_instance_template", "code_prompt"):
        assert getattr(ds, field) == getattr(native, field)
    for field in ("partition", "cpus_per_task", "mem", "time", "max_running_array_tasks", "max_task_attempts"):
        assert getattr(ds.hpc, field) == getattr(native.hpc, field)
    assert ds.agent_backends == {} and set(native.agent_backends) == {"plan", "code"}
    assert "exactly one executable bash" not in native.code_instance_template
    old = load_optimization_config("configs/gepa_safe_pce_verified_smoke_v1_20261022.yaml", require_api_keys=False)
    new = load_optimization_config("configs/gepa_safe_pce_verified_codex_smoke_v1_20261023.yaml", require_api_keys=False)
    assert old.search == new.search
    for field in ("reflector_model", "curator_model", "refiner_model", "prompt_bundle", "max_items", "condition_tokens", "item_guide_tokens"):
        assert getattr(old.hybrid, field) == getattr(new.hybrid, field)
    assert old.run_dir != new.run_dir
    assert SafePCEBackend(new).pce.agent_backends == native.agent_backends


def test_shared_job_credentials_match_native_and_mixed_agent_selection(tmp_path):
    from src.swe_verified_pce.config import load_swe_verified_pce_config
    from src.swe_verified_pce.hpc_executor import build_array_script
    native = load_swe_verified_pce_config(
        "configs/safe_pce_ace_verified_codex_smoke_source_v2_20261023.yaml", require_api_keys=False)
    ds = load_swe_verified_pce_config(
        "configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml", require_api_keys=False)
    def script(config):
        return build_array_script(config=config, batch_dir=tmp_path, indices=[0], attempt=1)
    assert "DEEPSEEK_API_KEY" not in script(native)
    mixed = replace(native, code=ds.code, agent_backends={"plan": native.agent_backends["plan"]})
    assert 'test -n "${DEEPSEEK_API_KEY:-}"' in script(mixed)


def test_v2_smoke_preserves_science_and_uses_new_frozen_inputs():
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
    from src.optimization.safe_pce_learning import SafePCEBackend
    old = load_optimization_config("configs/gepa_safe_pce_verified_codex_smoke_v1_20261023.yaml", require_api_keys=False)
    new = load_optimization_config("configs/gepa_safe_pce_verified_codex_smoke_v2_20261023.yaml", require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert old.search == new.search
    assert old.run_dir != new.run_dir and old.dataset_snapshot != new.dataset_snapshot
    for name in ("train.jsonl", "validation.jsonl"):
        assert (old.dataset_snapshot / name).read_bytes() == (new.dataset_snapshot / name).read_bytes()
    for name in ("reflector_model", "curator_model", "refiner_model", "prompt_bundle",
                 "condition_tokens", "item_guide_tokens", "max_items"):
        assert getattr(old.hybrid, name) == getattr(new.hybrid, name)
    left, right = SafePCEBackend(old).pce, SafePCEBackend(new).pce
    for name in ("plan", "code", "plan_prompt", "plan_instance_template", "code_prompt",
                 "code_instance_template", "agent_backends", "docker", "container"):
        assert getattr(left, name) == getattr(right, name)


def test_native_traces_survive_shared_learning_reference_projection():
    from src.optimization.agent_references import phase_references
    from tests.test_optimization.test_safe_pce_learning import ARTIFACT, _output
    record = {"instance_id":"Org__Repo-1", "split":"train", "task_semantics":"safe_pce_resolution_v1",
              "pce_runs":[{"pce_output":_output("Org__Repo-1")}], "planner_input":{"issue_description":"Issue"}}
    native = [{"role":"codex_cli", "content":{"raw_stdout":"full jsonl plan-00001 case-01",
               "events":[{"type":"item.completed", "item":{"id":"literal-id", "command":"cat file"}}]}}]
    record["pce_runs"][0]["pce_output"].update(plan_trajectory=deepcopy(native), code_trajectory=deepcopy(native))
    projected, _, _ = phase_references("reflector", {"parent":ARTIFACT,"record":record})
    assert projected["record"]["pce_runs"][0]["pce_output"]["plan_trajectory"] == native
    assert projected["record"]["pce_runs"][0]["pce_output"]["code_trajectory"] == native
