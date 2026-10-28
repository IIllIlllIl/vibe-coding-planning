"""Thin Native PreToolUse adapter over the existing source-acquisition policy.

The hook bundle contains only this adapter, the shared policy and its existing
pure-Python parser. It has no model, workflow, credentials or Agent loop.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
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
    _append_audit(audit_path, record)
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


def validate_native_submission(raw, audit_path, *, contract, output_dir):
    """Audit Stop delivery; final acceptance belongs to role content checks."""
    records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    if not records or any(not isinstance(r, dict) or r.get("policy_error") for r in records):
        raise ValueError("Native submission-policy receipts missing or invalid")
    final = records[-1]
    if (final.get("accepted") is not True or final.get("phase") != contract["phase"]
            or final.get("submission_sha256") != hashlib.sha256(raw.encode()).hexdigest()
            or final.get("raw_submission") != raw):
        raise ValueError("Native final response lacks a matching accepted Stop receipt")
    if contract["phase"] == "code":
        patch = output_dir / "submission.patch"
        if (patch.is_symlink() or not patch.is_file()
                or final.get("patch_sha256") != hashlib.sha256(patch.read_bytes()).hexdigest()):
            raise ValueError("Native patch changed after Stop validation")
    return records


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
        # Native hook errors can otherwise fail open. Explicitly deny instead;
        # the missing/error audit additionally invalidates the Host completion.
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


def _match_redacted_exports(items, originals, allowed, denied):
    """Require a complete one-to-one cover, even when exports look identical."""
    patterns = [re.compile(".*?".join(re.escape(part) for part in
                source_command(item["command"]).split("[REDACTED_SECRET]")), re.DOTALL)
                for item in items]
    slots = [(digest, True) for digest, count in allowed.items() for _ in range(count)]
    slots += [(digest, False) for digest, count in denied.items() for _ in range(count)]
    matches = {}

    def assign(slot, visited):
        digest, permitted = slots[slot]
        for index, item in enumerate(items):
            if (index in visited or not patterns[index].fullmatch(originals[digest])
                    or (not permitted and not (item.get("status") in {"declined", "failed"}
                        and item.get("exit_code") in {None, 126}))):
                continue
            visited.add(index)
            if index not in matches or assign(matches[index], visited):
                matches[index] = slot
                return True
        return False

    # All executed receipts are mandatory; denied calls may have no public item.
    for slot, (_, permitted) in enumerate(slots):
        found = assign(slot, set())
        if permitted and not found:
            raise ValueError("Native redacted exports lack complete original execution receipts")
    if len(matches) != len(items):
        raise ValueError("Native redacted command has no matching execution-policy receipt")
    for slot in matches.values():
        digest, permitted = slots[slot]
        (allowed if permitted else denied)[digest] -= 1


def validate_native_commands(trajectory, audit_path: Path, *, context: dict, source_log: Path):
    """Match source decisions to original execution events, not callback counts.

    A native denial is not an executed command or a failed scientific attempt.
    Historical PostToolUse records are retained but are not acceptance inputs.
    Uncovered executed commands, malformed traces and source-policy failures
    remain invalid; no raw record or native event is rewritten.
    """
    records = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError("Malformed Native execution-policy receipt")
    before = [record for record in records if record.get("hook_event_name") != "PostToolUse"]
    if any(record.get("policy_error") for record in before):
        raise ValueError("Native command policy failed")
    modern = any(record.get("schema_version") == 2 for record in before)
    for record in before:
        if modern:
            if (record.get("schema_version") != 2
                    or not isinstance(record.get("command"), str)
                    or record.get("command_sha256") != command_digest(record["command"])):
                raise ValueError("Malformed Native original-command receipt")
            if record.get("hook_event_name") != "PreToolUse":
                raise ValueError("Malformed Native hook event")
        decision = record.get("decision")
        if (record.get("schema_version") != (2 if modern else 1)
                or not isinstance(record.get("tool_use_id"), str) or not record["tool_use_id"]
                or not isinstance(record.get("command_sha256"), str)
                or len(record["command_sha256"]) != 64
                or type(record.get("blocked")) is not bool
                or (decision is not None and not isinstance(decision, dict))
                or record.get("returncode") != (126 if record["blocked"] else None)
                or record["blocked"] != (decision is not None and decision.get("decision") == "block")):
            raise ValueError("Malformed Native execution-policy receipt")
    if len({record["tool_use_id"] for record in before}) != len(before):
        raise ValueError("Duplicate Native execution-policy receipts")
    if modern:
        for record in before:
            if command_source_decision(record["command"],
                    prompt_urls=frozenset(context["prompt_urls"])) != record["decision"]:
                raise ValueError("Native original command differs from its source-policy decision")
    commands = {}
    for message in trajectory:
        if message.get("role") != "codex_cli":
            continue
        content = message["content"]
        if content.get("malformed_event_lines"):
            raise ValueError("Native command trace has malformed events")
        for event in content.get("events", []):
            item = event.get("item", {})
            if not isinstance(item, dict):
                raise ValueError("Native command trace has a malformed tool item")
            if item.get("type") == "web_search":
                raise ValueError("Native command policy does not cover hosted web search")
            if item.get("type") == "command_execution":
                command = item.get("command")
                identity = item.get("id")
                if not isinstance(command, str) or not isinstance(identity, str) or not identity:
                    raise ValueError("Native command trace lacks command text or identity")
                if identity in commands and commands[identity]["command"] != command:
                    raise ValueError("Native command identity changed command text")
                commands[identity] = item
    allowed = Counter(record["command_sha256"] for record in before if not record["blocked"])
    denied = Counter(record["command_sha256"] for record in before if record["blocked"])
    originals = {record["command_sha256"]: source_command(record["command"])
                 for record in before} if modern else {}
    # Match exact exports first. A vendor-redacted export is only a view of
    # an original command already checked by the pre-execution source policy.
    # Never substitute guessed text into the trajectory or accept by count alone.
    redacted = []
    for item in commands.values():
        digest = command_digest(item["command"])
        if modern and "[REDACTED_SECRET]" in item["command"]:
            redacted.append(item)
            continue
        if allowed[digest]:
            decision = command_source_decision(originals.get(digest, item["command"]),
                                               prompt_urls=frozenset(context["prompt_urls"]))
            if decision is not None and decision["decision"] == "block":
                raise ValueError("Native command executed despite a disallowed source decision")
            allowed[digest] -= 1
        elif denied[digest] and item.get("status") in {"declined", "failed"} and item.get("exit_code") in {None, 126}:
            denied[digest] -= 1
        else:
            raise ValueError("Native command has no matching execution-policy receipt")
    if redacted:
        _match_redacted_exports(redacted, originals, allowed, denied)
    if commands and not records:
        raise ValueError("Native execution-policy receipts missing")
    if any(allowed.values()):
        raise ValueError("Native execution-policy receipt lacks a raw command event")
    source_log.parent.mkdir(parents=True, exist_ok=True)
    for record in before:
        decision = record["decision"]
        if decision is not None:
            event = {**decision, **context.get("context", {}), "executor": "codex_cli",
                     "tool_use_id": record["tool_use_id"], "command_sha256": record["command_sha256"],
                     "executed": False if record["blocked"] else None,
                     "execution_status": "blocked" if record["blocked"] else "permitted",
                     "returncode": record["returncode"]}
            # Original tool input is retained in protected hook receipts;
            # the public CLI event may independently contain vendor redaction.
            with source_log.open("a") as output:
                output.write(json.dumps(event, sort_keys=True) + "\n")
    return records
