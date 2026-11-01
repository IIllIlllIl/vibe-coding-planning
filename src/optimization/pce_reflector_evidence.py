"""Mechanical, file-backed Ref views of unchanged Safe PCE records.

These are navigation views, not replacement scientific outputs or diagnoses.
Unknown fields/formats stay inline; only recognized log/duplicate fields move
behind references into the complete original pce_runs archive.
"""
import ast
from collections import Counter
import hashlib
import json


def _pointer(parent, key):
    return parent + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _ref(archive, pointer, value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return {"evidence_ref": {"file": archive, "json_pointer": pointer},
            "json_bytes": len(encoded), "json_sha256": hashlib.sha256(encoded).hexdigest()}


def _logs(value, archive, pointer):
    """Retain metadata/unknown fields; reference only named tool-output bodies."""
    if not isinstance(value, dict):
        return value
    return {key: (_ref(archive, _pointer(pointer, key), item)
                  if key in {"output", "stdout", "stderr", "aggregated_output", "raw_stdout"}
                  else item)
            for key, item in value.items()}


def _native_events(events, archive, pointer):
    if not isinstance(events, list):
        return events
    result = []
    for number, event in enumerate(events):
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "command_execution":
            path = _pointer(_pointer(pointer, number), "item")
            result.append({**event, "item": {key: (
                _ref(archive, _pointer(path, key), value) if key == "aggregated_output" else value)
                for key, value in item.items()}})
        else:
            result.append(event)
    return result


def _trajectory(value, archive, pointer):
    if not isinstance(value, list):
        return value  # Legacy/unknown formats are not silently discarded.
    result = []
    for number, message in enumerate(value):
        position = _pointer(pointer, number)
        if not isinstance(message, dict):
            result.append(message)
            continue
        content = message.get("content")
        role = message.get("role")
        if role == "codex_cli" and isinstance(content, dict):
            # Keep every event, including full model messages, exact commands,
            # file changes, errors, status and exit codes, in original order.
            native = {}
            for key, item in content.items():
                path = _pointer(_pointer(position, "content"), key)
                native[key] = (_ref(archive, path, item)
                    if key in {"raw_stdout", "effective_prompt", "stderr"}
                    else _native_events(item, archive, path) if key == "events" else item)
            result.append({**message, "content": native})
        elif role in {"host_codex_preflight", "host_evidence_manifest",
                      "host_command_policy", "host_submission_policy"}:
            # Duplicate audit receipts remain available; actual model/tool
            # behavior is above, not replaced by an execution verdict.
            result.append({**message, "content": _ref(archive, _pointer(position, "content"), content)})
        elif isinstance(content, dict) and (role == "tool" or (
                role == "user" and "action" in content and "returncode" in content)):
            result.append({**message, "content": _logs(content, archive, _pointer(position, "content"))})
        elif role == "user" and isinstance(content, str) and content.startswith("Observation: "):
            # mini-SWE v1 renders a Python dict. Parse literals only, never run
            # code or infer a command from prose. Preserve the exact source text.
            try:
                observation = ast.literal_eval(content[len("Observation: "):])
            except (ValueError, SyntaxError, TypeError, RecursionError):
                observation = None
            if isinstance(observation, dict) and "action" in observation and "returncode" in observation:
                # The parsed observation is a view of a STRING, so references
                # point to that original string, not nonexistent child pointers.
                body = {key: (_ref(archive, _pointer(position, "content"), content)
                              if key in {"output", "stdout", "stderr"} else item)
                        for key, item in observation.items()}
                result.append({**message, "content": {
                    "source": _ref(archive, _pointer(position, "content"), content),
                    "literal_observation": body}})
            else:
                result.append(message)
        else:
            result.append(message)
    return result


def _report(value, archive, pointer):
    """Index pass lists in official reports, preserving every other entry."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return value
    return {key: ({**_ref(archive, _pointer(pointer, key), item), "count": len(item)}
                  if key == "success" and isinstance(item, list)
                  else _report(item, archive, _pointer(pointer, key)))
            for key, item in value.items()}


def _evaluator(value, archive, pointer):
    """Expose unchanged outcomes and all non-pass test entries, not log bodies."""
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        path = _pointer(pointer, key)
        if key in {"raw_test_output", "raw_print_output", "stage_logs",
                   "output", "stdout", "stderr"}:
            result[key] = _ref(archive, path, item)
        elif key in {"test_status", "parsed_test_result"} and isinstance(item, dict) and all(
                isinstance(status, str) for status in item.values()):
            result[key] = {"source": _ref(archive, path, item),
                "status_counts": dict(Counter(item.values())),
                "nonpassing": {name: status for name, status in item.items()
                               if status.casefold() not in {"pass", "passed"}}}
        elif key in {"official_report", "rpg_report"}:
            result[key] = _report(item, archive, path)
        elif key == "execution_observation":
            result[key] = _logs(item, archive, path)
        else:
            # Failure, missing, skipped and unknown report fields remain full.
            result[key] = item
    return result


def pce_reflector_views(record, archive):
    """Return required view files and their index; perform no IO or mutation."""
    files, runs = {}, []
    for number, run in enumerate(record["pce_runs"]):
        prefix = f"record/pce/run-{number + 1:02d}"
        pointer = f"/{number}"
        output = run["pce_output"]
        output_pointer = pointer + "/pce_output"
        paths = {name: f"{prefix}/{name}.json" for name in (
            "run", "plan", "patch", "test_patch", "code_rejections", "plan_behavior", "code_behavior", "evaluator")}
        fields = {"plan": "plan", "patch": "patch", "plan_trajectory": "plan_behavior",
                  "code_trajectory": "code_behavior", "evaluator_result": "evaluator", "test_patch": "test_patch"}
        indexed = {}
        for key, item in output.items():
            path = _pointer(output_pointer, key)
            if key == "code_rejections":
                files[paths[key]] = [{**rejection, "submission": {
                    **rejection["submission"], "trajectory": _trajectory(rejection["submission"]["trajectory"],
                        archive, _pointer(_pointer(_pointer(path, i), "submission"), "trajectory"))},
                    "evaluator_evidence": _evaluator(rejection["evaluator_evidence"], archive,
                        _pointer(_pointer(path, i), "evaluator_evidence"))}
                    for i, rejection in enumerate(item)]
                indexed[key] = {"file": paths[key]}
            elif key in fields:
                name = fields[key]
                files[paths[name]] = (_trajectory(item, archive, path) if key.endswith("_trajectory")
                    else _evaluator(item, archive, path) if key == "evaluator_result" else item)
                indexed[key] = {"file": paths[name]}
            elif key in {"raw_plan_submission", "raw_patch", "code_workspace_evidence", "last_worker_output",
                         "source_access", "patch_submission"}:
                indexed[key] = _ref(archive, path, item)
            else:
                indexed[key] = item
        files[paths["run"]] = {
            "physical_metadata": {key: item for key, item in run.items() if key != "pce_output"},
            "pce_output": indexed, "original_run": _ref(archive, pointer, run)}
        runs.append({"run_number": number + 1,
                     **{key: path for key, path in paths.items() if path in files}})
    return files, {"schema_version": 1, "runs": runs,
        "reference_format": "evidence_ref gives a relative JSON file and an RFC 6901 JSON pointer. "
            "json_sha256/json_bytes describe the referenced value serialized as UTF-8 JSON "
            "with ensure_ascii=false, sort_keys=true and separators=(',', ':'). "
            "Views retain all recorded model messages and command/file-change events; "
            "tool-output bodies, duplicate audit receipts and full test reports remain in the archive. "
            "For literal_observation, read the referenced original mini-SWE observation string "
            "for its output fields. Consult referenced bodies when assessing evidence or attribution."}


def verify_pce_reflector_views(root, record, index):
    """Audit view fidelity as well as the separately preserved original record."""
    view = index.get("pce_evidence")
    if view is None:
        return  # Historical layouts retain their original audit contract.
    files, expected = pce_reflector_views(record, index["fields"]["pce_runs"])
    if view != expected:
        raise ValueError("PCE Reflector view index differs from original evidence")
    manifest = json.loads((root / "manifest.json").read_text())
    if (not set(files) <= set(manifest["required_files"])
            or index["fields"]["pce_runs"] not in manifest["optional_files"]):
        raise ValueError("PCE Reflector required/optional evidence classification differs")
    for path, value in files.items():
        if json.loads((root / path).read_text()) != value:
            raise ValueError(f"PCE Reflector view differs from original evidence: {path}")
