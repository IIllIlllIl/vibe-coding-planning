"""Common refusals, real portable hook callbacks, and Native audit integrity."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.agents.codex_hooks import (
    AUDIT_FILE, command_digest, hook_options, hook_result, validate_native_commands, write_bundle,
    SUBMISSION_AUDIT_FILE, stop_result,
)
from src.environment.source_access import command_source_decision, source_denial_result

CONTEXT = {"prompt_urls": ["https://docs.example/issue"],
           "context": {"instance_id": "synthetic", "phase": "code"}}


def event(command, identity="call-1"):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": identity,
            "tool_input": {"command": command}}


def native_items(*items):
    return [{"role": "codex_cli", "content": {"events": [
        {"type": "item.completed", "item": dict(type="command_execution", id=str(i), **item)}
        for i, item in enumerate(items)]}}]


def executed(command, audit, identity="call-1"):
    """Historical audit fixture; new calls no longer collect PostToolUse."""
    value = {"schema_version": 2, "hook_event_name": "PostToolUse",
             "tool_use_id": identity, "command": command, "command_sha256": command_digest(command)}
    with audit.open("a") as output:
        output.write(json.dumps(value) + "\n")


@pytest.mark.parametrize("command", [
    "pip install Werkzeug==2.2.3", "/opt/miniconda3/envs/testbed/bin/python -m pip install --target /tmp/deps Werkzeug==2.2.3",
    "git fetch origin", "curl https://github.com/org/repo/pull/1.patch",
    "/bin/bash -lc 'pip install Werkzeug==2.2.3'",
])
def test_native_and_miniswe_get_identical_refusal_before_execution(tmp_path, monkeypatch, command):
    from tests.test_environment.test_apptainer_env import _make_env
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw:
        calls.append(args) or subprocess.CompletedProcess(args, 0, stdout="ok", stderr=""))
    environment = _make_env(tmp_path / "sifs", source_access_log_path=tmp_path / "mini.jsonl")
    calls.clear()
    mini = environment.execute(command)
    assert calls == []
    reply = hook_result(event(command), CONTEXT, tmp_path / AUDIT_FILE)
    native = reply["hookSpecificOutput"]
    assert mini["returncode"] == 126
    assert native["permissionDecision"] == "deny"
    assert native["permissionDecisionReason"] == mini["output"]
    assert json.loads((tmp_path / AUDIT_FILE).read_text())["blocked"] is True
    # The same mini-SWE environment can immediately continue with a benign command.
    assert environment.execute("git status --short")["returncode"] == 0


@pytest.mark.parametrize("command", ["git status --short", "pip install --no-deps -e .",
    "curl https://docs.example/issue", "printf 'pip install is text'", "python -m pytest tests/test_blueprints.py"])
def test_allowed_commands_are_not_rewritten(tmp_path, command):
    assert hook_result(event(command), CONTEXT, tmp_path / AUDIT_FILE) == {}
    record = json.loads((tmp_path / AUDIT_FILE).read_text())
    assert record["blocked"] is False and record["command_sha256"] == command_digest(command)
    assert record["command"] == command


def test_portable_hook_uses_frozen_parser_and_returns_real_deny(tmp_path):
    directory = tmp_path / "policy"
    write_bundle(directory, CONTEXT)
    audit = tmp_path / AUDIT_FILE
    denied = "pip install synthetic-package && touch SHOULD_NEVER_EXIST"
    result = subprocess.run([sys.executable, "-S", str(directory / "policy.pyz"),
        str(directory / "context.json"), str(audit)], input=json.dumps(event(denied)),
        capture_output=True, text=True, check=True)
    reply = json.loads(result.stdout)["hookSpecificOutput"]
    assert reply["permissionDecision"] == "deny" and "126" in reply["permissionDecisionReason"]
    assert not (tmp_path / "SHOULD_NEVER_EXIST").exists()
    probe = subprocess.run([sys.executable, "-S", str(directory / "policy.pyz"), "--self-test"],
        capture_output=True, text=True, check=True)
    assert json.loads(probe.stdout)["policy_probe"] == "passed"
    # Allow a second tool call in the same session without repairing any Agent text.
    recovered = subprocess.run([sys.executable, "-S", str(directory / "policy.pyz"),
        str(directory / "context.json"), str(audit)], input=json.dumps(event("git status", "call-2")),
        capture_output=True, text=True, check=True)
    assert json.loads(recovered.stdout) == {}


def test_bad_hook_input_returns_explicit_denial_and_invalidates_completion(tmp_path):
    directory = tmp_path / "policy"
    write_bundle(directory, CONTEXT)
    audit = tmp_path / AUDIT_FILE
    result = subprocess.run([sys.executable, str(directory / "policy.pyz"),
        str(directory / "context.json"), str(audit)], input='{"invalid": true}',
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    with pytest.raises(ValueError, match="policy failed"):
        validate_native_commands([], audit, context=CONTEXT, source_log=tmp_path / "source.jsonl")


def test_denial_then_correction_can_complete_without_mutating_raw_trace(tmp_path):
    audit = tmp_path / AUDIT_FILE
    hook_result(event("pip install Werkzeug==2.2.3"), CONTEXT, audit)
    hook_result(event("python -m pytest tests/test_blueprints.py", "call-2"), CONTEXT, audit)
    executed("python -m pytest tests/test_blueprints.py", audit, "call-2")
    trace = native_items(dict(command="/bin/bash -lc 'pip install Werkzeug==2.2.3'", status="failed", exit_code=None),
                         dict(command="/bin/bash -lc 'python -m pytest tests/test_blueprints.py'", status="completed", exit_code=0))
    original = deepcopy(trace)
    rows = validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert trace == original and len(rows) == 3
    rejection = json.loads((tmp_path / "sources.jsonl").read_text())
    assert rejection["executed"] is False and rejection["returncode"] == 126


@pytest.mark.parametrize("command,export", [
    ("token=synthetic-fixture-value; printf ok", "token=[REDACTED_SECRET] printf ok"),
    ("python -c \"AsyncOpenAI(api_key='sk-test-offline')\"",
     "python -c \"AsyncOpenAI(api_key='[REDACTED_SECRET]')\""),
    ("python - <<'PY'\ntoken = data.get('name')\nprint(token)\nPY",
     "python - <<'PY'\ntoken = [REDACTED_SECRET]\nprint(token)\nPY"),
])
def test_vendor_redaction_is_matched_to_original_pre_receipts_without_rewriting(tmp_path, command, export):
    audit = tmp_path / AUDIT_FILE
    hook_result(event(command), CONTEXT, audit)
    trace = native_items(dict(command=export, status="completed", exit_code=0))
    original = deepcopy(trace)
    rows = validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert trace == original and len(rows) == 1 and rows[0]["command"] == command


@pytest.mark.parametrize("post", ["missing", "matching", "orphan", "changed", "duplicate", "error"])
def test_legacy_post_receipts_are_not_completion_requirements(tmp_path, post):
    command = "token=synthetic-fixture-value; printf ok"
    audit = tmp_path / AUDIT_FILE
    hook_result(event(command), CONTEXT, audit)
    if post not in {"missing", "error"}:
        executed(command if post != "changed" else command + " changed", audit,
                 "unknown-call" if post == "orphan" else "call-1")
    if post == "duplicate":
        executed(command, audit)
    if post == "error":
        with audit.open("a") as output:
            output.write(json.dumps({"hook_event_name": "PostToolUse", "policy_error": True}) + "\n")
    trace = native_items(dict(command="token=[REDACTED_SECRET] printf ok", status="completed", exit_code=0))
    original, raw = deepcopy(trace), audit.read_bytes()
    validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert trace == original and audit.read_bytes() == raw


def test_redacted_export_cannot_substitute_a_different_executed_command(tmp_path):
    audit = tmp_path / AUDIT_FILE
    hook_result(event("token=synthetic-fixture-value; printf ok"), CONTEXT, audit)
    trace = native_items(dict(command="token=[REDACTED_SECRET] git fetch origin", status="completed", exit_code=0))
    with pytest.raises(ValueError):
        validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")


@pytest.mark.parametrize("post_count", [0, 37, 39])
def test_39_permitted_commands_complete_with_any_post_coverage(tmp_path, post_count):
    """Regression for the v5 failure shape; no model or Slurm invocation."""
    audit = tmp_path / AUDIT_FILE
    commands = [f"grep -n 'fixture-{index}' repository.py" for index in range(39)]
    for index, command in enumerate(commands):
        hook_result(event(command, f"call-{index}"), CONTEXT, audit)
        if index < post_count:
            executed(command, audit, f"call-{index}")
    trace = native_items(*(dict(command=command, status="completed", exit_code=0) for command in commands))
    # Native item IDs are not hook IDs, and concurrent completions may be reordered.
    trace[0]["content"]["events"].reverse()
    original, raw = deepcopy(trace), audit.read_bytes()
    rows = validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert len(rows) == 39 + post_count
    assert trace == original and audit.read_bytes() == raw


def test_post_receipt_cannot_replace_a_missing_pre_decision(tmp_path):
    audit = tmp_path / AUDIT_FILE
    executed("git status", audit)
    trace = native_items(dict(command="git status", status="completed", exit_code=0))
    with pytest.raises(ValueError, match="no matching execution-policy receipt"):
        validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")


def test_legacy_receipts_still_require_exact_exports(tmp_path):
    command = "token=synthetic-fixture-value; printf ok"
    record = {"schema_version": 1, "tool_use_id": "call-1", "command_sha256": command_digest(command),
              "decision": None, "blocked": False, "returncode": None}
    audit = tmp_path / AUDIT_FILE
    audit.write_text(json.dumps(record) + "\n")
    exact = native_items(dict(command=command, status="completed", exit_code=0))
    validate_native_commands(exact, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    changed = native_items(dict(command="token=[REDACTED_SECRET] printf ok", status="completed", exit_code=0))
    with pytest.raises(ValueError, match="no matching execution-policy receipt"):
        validate_native_commands(changed, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")


def test_distinct_original_commands_with_identical_redacted_exports_keep_full_coverage(tmp_path):
    audit = tmp_path / AUDIT_FILE
    for index, value in enumerate(("first-fixture", "second-fixture")):
        command = f"token={value}; printf ok"
        hook_result(event(command, f"call-{index}"), CONTEXT, audit)
    item = dict(command="token=[REDACTED_SECRET] printf ok", status="completed", exit_code=0)
    trace = native_items(item, item)
    original = deepcopy(trace)
    validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert trace == original
    with pytest.raises(ValueError, match="complete original execution receipts"):
        validate_native_commands(native_items(item), audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")


@pytest.mark.parametrize("problem", ["missing", "actually_executed", "duplicate", "wrong_command", "web_search", "malformed", "permitted_without_trace"])
def test_policy_audit_failures_cannot_become_valid_completion(tmp_path, problem):
    audit = tmp_path / AUDIT_FILE
    audit.write_text("")
    trace = native_items(dict(command="pip install Werkzeug", status="failed", exit_code=None))
    if problem != "missing": hook_result(event("pip install Werkzeug"), CONTEXT, audit)
    if problem == "actually_executed": trace[0]["content"]["events"][0]["item"].update(status="completed",exit_code=0)
    if problem == "duplicate": hook_result(event("pip install Werkzeug"), CONTEXT, audit)
    if problem == "wrong_command": trace[0]["content"]["events"][0]["item"]["command"] = "git fetch origin"
    if problem == "web_search": trace[0]["content"]["events"].append({"item": {"type": "web_search"}})
    if problem == "malformed": trace[0]["content"]["malformed_event_lines"] = ["not-json"]
    if problem == "permitted_without_trace":
        hook_result(event("git status", "call-2"), CONTEXT, audit)
    with pytest.raises(ValueError):
        validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")


def test_native_hook_cli_options_keep_sandbox_and_user_config_separate(tmp_path):
    from src.optimization.codex_cli_runtime import _codex_command
    options = hook_options("/opt/miniconda3/envs/testbed/bin/python")
    cmd = _codex_command({"executor":"codex_cli","model":"mock","reasoning_effort":"high"},
        working_directory=tmp_path, output_path=tmp_path/"out", repository_writable=True,
        command_hook_options=tuple(options))
    assert "--ignore-user-config" in cmd and "hooks.PreToolUse=" in " ".join(cmd)
    assert "hooks.PostToolUse=" not in " ".join(cmd)
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert 'web_search="disabled"' in cmd


@pytest.mark.parametrize("problem", ["nonobject_receipt", "inconsistent_receipt", "nonobject_item", "missing_identity", "changed_command"])
def test_malformed_audit_is_explicit_validation_failure(tmp_path, problem):
    audit = tmp_path / AUDIT_FILE
    hook_result(event("git status"), CONTEXT, audit)
    executed("git status", audit)
    trace = native_items(dict(command="git status", status="completed", exit_code=0))
    if problem == "nonobject_receipt":
        audit.write_text("[]\n")
    if problem == "inconsistent_receipt":
        receipts = [json.loads(line) for line in audit.read_text().splitlines()]
        receipts[0]["blocked"] = True
        audit.write_text("".join(json.dumps(receipt) + "\n" for receipt in receipts))
    if problem == "nonobject_item":
        trace[0]["content"]["events"][0]["item"] = None
    if problem == "missing_identity":
        del trace[0]["content"]["events"][0]["item"]["id"]
    if problem == "changed_command":
        changed = deepcopy(trace[0]["content"]["events"][0])
        changed["item"]["command"] = "pip install synthetic-package"
        trace[0]["content"]["events"].append(changed)
    original = deepcopy(trace)
    with pytest.raises(ValueError):
        validate_native_commands(trace, audit, context=CONTEXT, source_log=tmp_path / "sources.jsonl")
    assert trace == original


@pytest.mark.parametrize("failure,submission", [(None, False), ("bundle_probe", False),
    ("feature_config", False), ("missing_receipt", False), (None, True),
    ("missing_stop_receipt", True), ("invalid_stop_receipt", True)])
def test_production_transport_wires_policy_mounts_preflights_and_original_outputs(tmp_path, monkeypatch, failure, submission):
    from types import SimpleNamespace
    from tests.test_optimization.test_codex_sif_execution import _inputs
    from src.optimization import codex_cli_runtime as runtime
    sif, evidence, repo, model = _inputs(tmp_path)
    model["codex_version"] = "0.160.1"
    log = tmp_path / "sources.jsonl"
    context = {**CONTEXT, **({"submission_contract": {"phase": "plan"}} if submission else {})}
    boundary = runtime.CodexSIFExecution(sif, evidence, repo, repository_writable=True,
        output_files=("codex_final_response.txt",), source_policy=context, source_access_log=log)
    inference = []
    final = b"START_PLAN\n# Plan\nOriginal {{data}}\nEND_PLAN\n"
    def run(command, **kwargs):
        mounts = {command[i+1].split(":")[1]: Path(command[i+1].split(":")[0])
                  for i,v in enumerate(command) if v=="--bind"}
        assert str(mounts["/opt/vibe-agent-policy"])+":/opt/vibe-agent-policy:ro" in command
        assert "/agent-policy-audit" in mounts
        if "sif-python-probe" in command:
            return SimpleNamespace(returncode=0, stdout="/opt/miniconda3/envs/testbed/bin/python\n", stderr="")
        if command[-1]=="--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.160.1\n", stderr="")
        if "--self-test" in command:
            return SimpleNamespace(returncode=1 if failure=="bundle_probe" else 0,
                stdout='{"policy_probe": "passed"}', stderr="")
        if command[-2:]==["features","list"]:
            assert any("hooks.PreToolUse=" in a for a in command)
            assert any("hooks.Stop=" in a for a in command) == submission
            return SimpleNamespace(returncode=0, stdout="hooks stable "+("false" if failure=="feature_config" else "true"), stderr="")
        if "exec" in command and command[-1]=="-":
            inference.append(command)
            assert "--dangerously-bypass-hook-trust" in command
            assert "--dangerously-bypass-approvals-and-sandbox" not in command
            audit=mounts["/agent-policy-audit"]/AUDIT_FILE
            if failure!="missing_receipt":
                hook_result(event("pip install Werkzeug"), CONTEXT, audit)
                hook_result(event("git status", "call-2"), CONTEXT, audit)
            (mounts["/agent-output"]/"codex_final_response.txt").write_bytes(final)
            if submission and failure != "missing_stop_receipt":
                stop_audit = mounts["/agent-policy-audit"] / SUBMISSION_AUDIT_FILE
                stop_result({"hook_event_name": "Stop", "last_assistant_message": "START_PLAN\n# Plan\nEND_PLAN"}, context, stop_audit)
                if failure != "invalid_stop_receipt":
                    stop_result({"hook_event_name": "Stop", "last_assistant_message": final.decode()}, context, stop_audit)
            trace=native_items(dict(command="git status",status="completed",exit_code=0))
            return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in trace[0]["content"]["events"]), stderr="")
        return SimpleNamespace(returncode=0, stdout="",stderr="")
    monkeypatch.setattr(runtime.subprocess,"run",run)
    args=dict(container=boundary,model_config=model,working_directory=repo,attempt_dir=tmp_path/"attempt",
              system="Plan",user="Input",task="Plan")
    if failure and failure not in {"missing_stop_receipt", "invalid_stop_receipt"}:
        with pytest.raises(runtime.CodexConfigurationError) as caught:
            runtime.run_codex_text_agent(**args)
        assert bool(inference)==(failure not in {"bundle_probe", "feature_config"})
        if inference:
            assert (tmp_path/"attempt/codex_final_response.txt").read_bytes()==final
            assert caught.value.trajectory[-1]["content"]==final.decode()
    else:
        text,trajectory=runtime.run_codex_text_agent(**args)
        assert text.encode()==final
        assert trajectory[-1]["content"].encode()==final
        assert json.loads(log.read_text())["executed"] is False
        assert json.loads((tmp_path/"attempt"/AUDIT_FILE).read_text().splitlines()[0])["blocked"]
        if failure in {"missing_stop_receipt", "invalid_stop_receipt"}:
            assert any(row.get("role") == "host_submission_policy"
                       and row["content"].get("diagnostic_error") for row in trajectory)
