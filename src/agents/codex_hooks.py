"""Thin Native PreToolUse adapter over the existing source-acquisition policy.

The hook bundle contains only this adapter, the shared policy and its existing
pure-Python parser. It has no model, workflow, credentials or Agent loop.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import subprocess
import zipfile

from src.environment.source_access import (
    command_source_decision, source_command, source_denial_result,
)

POLICY_DIR = "/opt/vibe-agent-policy"
AUDIT_DIR = "/agent-policy-audit"
AUDIT_FILE = "codex_tool_policy.jsonl"
SUBMISSION_AUDIT_FILE = "codex_submission_policy.jsonl"


def command_digest(command):
    return hashlib.sha256(source_command(command).encode()).hexdigest()


def write_bundle(directory: Path, context: dict) -> None:
    """Freeze reviewed code and bashlex; no pip/network access in a case SIF."""
    import bashlex
    from src.environment import source_access
    from src.agents import plan_submission
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "context.json").write_text(json.dumps(context, sort_keys=True))
    with zipfile.ZipFile(directory / "policy.pyz", "w", zipfile.ZIP_STORED) as bundle:
        for package in ("src", "src/agents", "src/environment"):
            bundle.writestr(package + "/__init__.py", "")
        bundle.writestr("__main__.py", "from src.agents.codex_hooks import main\nmain()\n")
        bundle.write(__file__, "src/agents/codex_hooks.py")
        bundle.write(source_access.__file__, "src/environment/source_access.py")
        bundle.write(plan_submission.__file__, "src/agents/plan_submission.py")
        parser = Path(bashlex.__file__).parent
        for path in sorted(parser.rglob("*.py")):
            bundle.write(path, "bashlex/" + path.relative_to(parser).as_posix())


def hook_options(interpreter: str, *, directory=POLICY_DIR, audit_directory=AUDIT_DIR, submission=False):
    command = shlex.join([interpreter, directory + "/policy.pyz", directory + "/context.json",
                          audit_directory + "/" + AUDIT_FILE])
    handler = '{type="command", command=' + json.dumps(command) + ', timeout=15}'
    groups = '[{matcher="^Bash$", hooks=[' + handler + ']}]'
    options = ["--config", "features.hooks=true", "--config", "hooks.PreToolUse=" + groups]
    if submission:
        stop_command = shlex.join([interpreter, directory + "/policy.pyz", directory + "/context.json",
                                  audit_directory + "/" + SUBMISSION_AUDIT_FILE])
        options += ["--config", 'hooks.Stop=[{hooks=[{type="command", command=' + json.dumps(stop_command)
                    + ', timeout=30}]}]']
    return [*options, "--dangerously-bypass-hook-trust"]


def hook_result(event, context, audit_path: Path):
    """An explicit deny is recoverable in the same native Agent turn."""
    command = event["tool_input"]["command"]
    if event.get("hook_event_name") != "PreToolUse" or event.get("tool_name") != "Bash":
        raise ValueError("unexpected source-policy hook event")
    if not isinstance(command, str) or not isinstance(event.get("tool_use_id"), str):
        raise ValueError("invalid source-policy tool arguments")
    decision = command_source_decision(command, prompt_urls=frozenset(context["prompt_urls"]))
    denied = decision is not None and decision["decision"] == "block"
    record = {"schema_version": 2, "hook_event_name": "PreToolUse",
              "tool_use_id": event["tool_use_id"], "command": command,
              "command_sha256": command_digest(command), "decision": decision,
              "blocked": denied, "returncode": 126 if denied else None}
    try:
        _append_audit(audit_path, record)
    except OSError:
        pass  # Recording cannot change the pre-execution decision.
    if denied:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": source_denial_result(decision["reason"])["output"]}}
    return {}


def _append_audit(path, record):
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def stop_result(event, context, audit_path, *, output_dir=Path("/agent-output")):
    """Keep an invalid final submission inside the existing Native loop."""
    from src.agents.plan_submission import validate_human_plan, validate_code_submission, submission_feedback
    contract = context["submission_contract"]
    phase = contract["phase"]
    raw = event.get("last_assistant_message")
    if event.get("hook_event_name") != "Stop" or not isinstance(raw, str) or phase not in {"plan", "code"}:
        raise ValueError("invalid Native submission hook event")
    record = {"schema_version": 1, "phase": phase, "raw_submission": raw,
              "submission_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    try:
        if phase == "plan":
            validate_human_plan(raw)
        else:
            patch_path = output_dir / "submission.patch"
            patch = patch_path.read_bytes() if patch_path.is_file() and not patch_path.is_symlink() else None
            validate_code_submission(raw, patch, allow_empty=contract["allow_empty"])
            staged = subprocess.run(["git", "diff", "--cached", "--binary", "--full-index"],
                cwd=contract["workdir"], capture_output=True, check=False, timeout=30)
            if staged.returncode:
                raise ValueError(f"staged diff verification failed (returncode={staged.returncode})")
            validate_code_submission(raw, patch, allow_empty=contract["allow_empty"],
                staged_sha256=hashlib.sha256(staged.stdout).hexdigest())
            record["patch_sha256"] = hashlib.sha256(patch).hexdigest()
    except ValueError as exc:
        record.update(accepted=False, error=str(exc))
        result = {"decision": "block", "reason": submission_feedback(str(exc), phase=phase)}
    else:
        record["accepted"] = True
        result = {}
    try:
        _append_audit(audit_path, record)
    except OSError:
        # Diagnostic persistence must not hide the corrective feedback or
        # reject valid content. Final role-level validation is independent.
        pass
    return result


def main():
    try:
        if sys.argv[1:] == ["--self-test"]:
            blocked = command_source_decision("pip install synthetic-policy-probe", prompt_urls=frozenset())
            assert blocked["decision"] == "block"
            assert command_source_decision("git status --short", prompt_urls=frozenset()) is None
            print(json.dumps({"policy_probe": "passed", "returncode": 126}))
            return
        context = json.loads(Path(sys.argv[1]).read_text())
        event = json.load(sys.stdin)
        is_stop = event.get("hook_event_name") == "Stop"
        handler = stop_result if is_stop else hook_result
        result = handler(event, context, Path(sys.argv[2]))
    except Exception:
        # A policy execution error cannot establish whether a command is allowed.
        try:
            _append_audit(Path(sys.argv[2]), {"policy_error": True})
        except Exception:
            pass
        if locals().get("is_stop", False):
            result = {"decision": "block", "reason": "Submission validation failed operationally. "
                      "The task is not complete; the Host has not changed your output."}
        else:
            result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                      "permissionDecisionReason": "Source-policy execution failed; command denied (returncode=126)."}}
    print(json.dumps(result))
