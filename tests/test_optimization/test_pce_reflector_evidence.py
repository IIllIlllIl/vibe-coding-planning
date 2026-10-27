"""Lossless Ref navigation over Native and mini-SWE behavior; no inference."""
from copy import deepcopy
import hashlib
import json

import pytest

from src.optimization.behavioral_hybrid_evidence import write_hybrid_evidence
from src.optimization.pce_reflector_evidence import verify_pce_reflector_views


def read(root, path):
    return json.loads((root / path).read_text())


def record(backend="codex", repetitions=2):
    large = "original tool output\n" * 50000
    events = [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Need both callers"}},
        {"type": "item.started", "item": {"type": "command_execution", "id": "a",
            "command": "rg caller .", "exit_code": None, "status": "in_progress", "aggregated_output": ""}},
        {"type": "item.completed", "item": {"type": "command_execution", "id": "a",
            "command": "rg caller .", "exit_code": 1, "status": "completed", "aggregated_output": large}},
        {"type": "item.completed", "item": {"type": "file_change", "status": "completed",
            "changes": [{"path": "src/a.py", "kind": "update", "diff": "+ important change"}]}},
        {"type": "new_event", "output": "unknown evidence stays inline"},
        {"type": "turn.completed", "usage": {"input_tokens": 42}},
    ]
    native = [{"role": "system", "content": "P/C prompt"},
        {"role": "codex_cli", "content": {"events": events, "returncode": 0,
            "raw_stdout": large, "stderr": "", "effective_prompt": "P/C prompt"}},
        {"role": "host_submission_policy", "content": {"raw_submission": large}},
        {"role": "assistant", "content": "Final model message"}]
    mini = [{"role": "assistant", "content": "Inspect callers\n```bash\nrg caller .\n```"},
        {"role": "user", "content": "Observation: " + repr(
            {"output": large, "returncode": 126, "action": "rg caller .", "policy": "denied"})},
        {"role": "assistant", "content": "Address caller compatibility"}]
    return {"instance_id": "case-01", "split": "train", "task_semantics": "safe_pce_resolution_v1",
        "planner_input": {"issue_description": "Preserve caller compatibility"}, "score": 0.5,
        "pce_runs": [{"score": n % 2, "repetition": n + 1, "unknown_metadata": [n],
            "pce_output": {"plan": "# Original Plan\n" + str(n), "patch": "diff original " + str(n),
                "status": "completed", "pce_status": "completed", "terminal_phase": "evaluate",
                "terminal_reason": "rpg_tests_unresolved", "raw_patch": "diff original " + str(n),
                "plan_trajectory": deepcopy(native if backend == "codex" else mini),
                "code_trajectory": deepcopy(native if backend == "codex" else mini),
                "unknown_output": {"output": "keep this unknown field"},
                "evaluator_result": {"evaluator_resolved": False, "test_timed_out": False,
                    "task_outcome": "unresolved", "stage_exit_codes": {"test": 1, "parser": 0},
                    "raw_test_output": large, "raw_print_output": large,
                    "test_status": {"test_ok": "pass", "test_bad/~x": "fail", "test_skip": "skip"},
                    "rpg_report": {"FAIL_TO_PASS": {"failure": ["test_bad/~x"], "missing": ["test_missing"],
                        "skipped": ["test_skip"], "success": [f"test_{i}" for i in range(10000)]}},
                    "unknown_evaluator": {"success": ["keep"], "output": "keep unknown"}}}}
            for n in range(repetitions)]}


def refs(value):
    if isinstance(value, dict):
        if "evidence_ref" in value:
            yield value
        for item in value.values():
            yield from refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from refs(item)


@pytest.mark.parametrize("backend", ["codex", "mini"])
def test_required_behavior_and_outcomes_exclude_long_logs_but_preserve_raw(tmp_path, backend):
    original = record(backend)
    before = deepcopy(original)
    manifest = write_hybrid_evidence(tmp_path, "reflector", {"parent": {"checklist": []}, "record": original})
    index = read(tmp_path, "record.json")
    archive = read(tmp_path, index["fields"]["pce_runs"])
    assert archive == original["pce_runs"] and original == before
    assert {key: read(tmp_path, path) for key, path in index["fields"].items()} == original
    assert manifest["optional_files"] == [index["fields"]["pce_runs"]]
    assert not set(manifest["required_files"]) & set(manifest["optional_files"])
    assert set(manifest["files"]) == set(manifest["required_files"] + manifest["optional_files"])
    assert sum((tmp_path / path).stat().st_size for path in manifest["required_files"]) < 40000
    assert (tmp_path / index["fields"]["pce_runs"]).stat().st_size > 8_000_000
    for n, paths in enumerate(index["pce_evidence"]["runs"]):
        out = original["pce_runs"][n]["pce_output"]
        assert read(tmp_path, paths["plan"]) == out["plan"]
        assert read(tmp_path, paths["patch"]) == out["patch"]
        assert read(tmp_path, paths["run"])["physical_metadata"] == {
            key: value for key, value in original["pce_runs"][n].items() if key != "pce_output"}
        assert read(tmp_path, paths["run"])["pce_output"]["unknown_output"] == out["unknown_output"]
        for key in ("plan_behavior", "code_behavior"):
            view = read(tmp_path, paths[key])
            if backend == "codex":
                source_events = out["plan_trajectory"][1]["content"]["events"]
                events = view[1]["content"]["events"]
                assert len(events) == len(source_events)
                for actual, source in zip(events, source_events, strict=True):
                    if source.get("item", {}).get("type") == "command_execution":
                        assert {k: v for k, v in actual["item"].items() if k != "aggregated_output"} == {
                            k: v for k, v in source["item"].items() if k != "aggregated_output"}
                    else:
                        assert actual == source
            else:
                assert view[0] == out["plan_trajectory"][0] and view[-1] == out["plan_trajectory"][-1]
                observation = view[1]["content"]["literal_observation"]
                assert (observation["action"], observation["returncode"], observation["policy"]) == (
                    "rg caller .", 126, "denied")
        evaluator = read(tmp_path, paths["evaluator"])
        assert evaluator["stage_exit_codes"] == out["evaluator_result"]["stage_exit_codes"]
        assert evaluator["test_status"]["nonpassing"] == {"test_bad/~x": "fail", "test_skip": "skip"}
        assert evaluator["test_status"]["status_counts"] == {"pass": 1, "fail": 1, "skip": 1}
        report = evaluator["rpg_report"]["FAIL_TO_PASS"]
        assert report["failure"] == ["test_bad/~x"] and report["missing"] == ["test_missing"]
        assert report["success"]["count"] == 10000
        assert evaluator["unknown_evaluator"] == out["evaluator_result"]["unknown_evaluator"]
    # Every view reference resolves to an untouched original JSON value; all
    # hashes/counts are mechanical and independently recomputed here.
    for path in manifest["required_files"]:
        for ref in refs(read(tmp_path, path)):
            value = read(tmp_path, ref["evidence_ref"]["file"])
            for component in ref["evidence_ref"]["json_pointer"].split("/")[1:]:
                component = component.replace("~1", "/").replace("~0", "~")
                value = value[int(component)] if isinstance(value, list) else value[component]
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            assert len(encoded) == ref["json_bytes"]
            assert hashlib.sha256(encoded).hexdigest() == ref["json_sha256"]
    verify_pce_reflector_views(tmp_path, original, index)


def test_unknown_and_unparseable_trajectory_formats_are_not_pruned(tmp_path):
    original = record("mini", repetitions=1)
    messages = [{"role": "user", "content": "Observation: custom nonliteral text"},
        {"role": "user", "content": "Observation: {'output': 'missing command/status'}"},
        {"role": "user", "content": {"output": "unknown structured input", "task": "keep all"}},
        {"role": "codex_cli", "content": "legacy unstructured Native text"},
        {"role": "new_role", "content": {"output": "unknown output", "a/b~c": "value"}}, 12]
    original["pce_runs"][0]["pce_output"]["plan_trajectory"] = messages
    write_hybrid_evidence(tmp_path, "reflector", {"parent": {}, "record": original})
    index = read(tmp_path, "record.json")
    assert read(tmp_path, index["pce_evidence"]["runs"][0]["plan_behavior"]) == messages
    verify_pce_reflector_views(tmp_path, original, index)


def test_structured_tool_observation_keeps_command_status_and_unknown_fields(tmp_path):
    original = record("mini", repetitions=1)
    observation = {"action": "git diff", "returncode": 1, "output": "original long output",
                   "unknown": {"output": "preserve unknown metadata"}}
    output = original["pce_runs"][0]["pce_output"]
    output["plan_trajectory"] = [{"role": "tool", "content": observation}]
    output["evaluator_result"]["parsed_test_result"] = {"test_ok": "PASSED", "test_error": "ERROR"}
    output["evaluator_result"]["official_report"] = {"case-01": {
        "resolved": False, "tests_status": {"PASS_TO_PASS": {
            "success": ["test_ok"], "failure": ["test_error"]}}}}
    write_hybrid_evidence(tmp_path, "reflector", {"parent": {}, "record": original})
    index = read(tmp_path, "record.json")
    paths = index["pce_evidence"]["runs"][0]
    view = read(tmp_path, paths["plan_behavior"])[0]["content"]
    assert {k: v for k, v in view.items() if k != "output"} == {
        k: v for k, v in observation.items() if k != "output"}
    assert "evidence_ref" in view["output"]
    evaluator = read(tmp_path, paths["evaluator"])
    assert evaluator["parsed_test_result"]["nonpassing"] == {"test_error": "ERROR"}
    report = evaluator["official_report"]["case-01"]["tests_status"]["PASS_TO_PASS"]
    assert report["failure"] == ["test_error"] and report["success"]["count"] == 1
    verify_pce_reflector_views(tmp_path, original, index)


@pytest.mark.parametrize("corruption", ["plan", "behavior", "classification"])
def test_view_audit_rejects_changed_plan_or_missing_behavior(tmp_path, corruption):
    original = record(repetitions=1)
    write_hybrid_evidence(tmp_path, "reflector", {"parent": {}, "record": original})
    index = read(tmp_path, "record.json")
    paths = index["pce_evidence"]["runs"][0]
    if corruption == "plan":
        (tmp_path / paths["plan"]).write_text(json.dumps("Host-rewritten Plan"))
    elif corruption == "behavior":
        (tmp_path / paths["code_behavior"]).write_text("[]")
    else:
        manifest = read(tmp_path, "manifest.json")
        manifest["required_files"].remove(paths["code_behavior"])
        manifest["optional_files"].append(paths["code_behavior"])
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="differs"):
        verify_pce_reflector_views(tmp_path, original, index)


def test_historical_record_without_pce_views_keeps_original_layout(tmp_path):
    original = {"instance_id": "case-01", "checker_output": {"trajectory": ["full investigation"]}}
    manifest = write_hybrid_evidence(tmp_path, "reflector", {"parent": {}, "record": original})
    index = read(tmp_path, "record.json")
    assert "pce_evidence" not in index and manifest["optional_files"] == []
    assert set(index["fields"].values()) <= set(manifest["required_files"])
    verify_pce_reflector_views(tmp_path, original, index)
