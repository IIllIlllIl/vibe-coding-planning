import copy

import pytest
from src.optimization.agent_references import phase_references, decode_phase_output

from src.optimization.behavioral_hybrid_ace import (
    curator_case_projection, resolve_curator_case_references,
)


def test_case_projection_preserves_authority_and_scientific_text():
    payload = {"records": [{"instance_id": "long-uuid#first-plan", "plan": "long-uuid#first-plan"}],
               "reviews": [{"instance_id": "long-uuid#first-plan"}],
               "history": {"events": [{"instance_id": "old-case"}]}}
    before = copy.deepcopy(payload)
    projected, mapping = curator_case_projection(payload)
    assert payload == before
    assert projected["records"][0]["instance_id"] == "case-01"
    assert projected["records"][0]["plan"] == "long-uuid#first-plan"
    assert projected["reviews"][0]["instance_id"] == "case-01"
    assert projected["history"]["events"][0]["instance_id"] == "old-case"
    delta = {"operations": [{"type": "UPDATE", "supporting_instance_ids": ["case-01"],
                              "content": {"condition": "case-01", "guide": "unchanged"}}]}
    decoded = resolve_curator_case_references(delta, mapping)
    assert delta["operations"][0]["supporting_instance_ids"] == ["case-01"]
    assert decoded["operations"][0]["supporting_instance_ids"] == ["long-uuid#first-plan"]
    assert decoded["operations"][0]["content"] == delta["operations"][0]["content"]


def test_unknown_reference_is_rejected_with_specific_feedback():
    with pytest.raises(ValueError, match="operation 1: unknown reference.*allowed references"):
        resolve_curator_case_references({"operations": [{"supporting_instance_ids": ["case-99"]}]},
                                        {"case-01": "original"})
    assert resolve_curator_case_references({"operations": [{"supporting_instance_ids": []}]}, {})


@pytest.mark.parametrize("role", ["reflector", "curator", "refiner"])
def test_shared_projection_and_exact_role_decode(role):
    artifact = {"checklist": [{"id": "plan-00006", "condition": "keep plan-00006", "guide": "guide"},
                               {"id": "plan-00009", "condition": "second", "guide": "guide"}]}
    payload = ({"draft": artifact} if role == "refiner" else
               {"parent": artifact, **({"record": {"instance_id": "uuid"}} if role == "reflector"
                                       else {"records": [{"instance_id": "uuid"}],
                                             "parent_counter": {"id": "plan-00006"}})})
    original = copy.deepcopy(payload)
    projected, cases, items = phase_references(role, payload)
    assert payload == original
    view = projected.get("parent", projected.get("draft"))
    assert view["checklist"][0]["id"] == "item-01"
    assert view["checklist"][0]["condition"] == "keep plan-00006"
    if role == "reflector":
        output = {"instance_id": "case-01", "bullet_tags": [{"id": "item-02", "tag": "helpful"}]}
        decoded = decode_phase_output(role, output, cases, items)
        assert decoded["instance_id"] == "uuid"
        assert decoded["bullet_tags"][0]["id"] == "plan-00009"
    elif role == "curator":
        output = {"operations": [{"type": "MERGE", "target_ids": ["item-01", "item-02"],
                                   "supporting_instance_ids": ["case-01"]}]}
        decoded = decode_phase_output(role, output, cases, items)
        assert decoded["operations"][0]["target_ids"] == ["plan-00006", "plan-00009"]
        assert decoded["operations"][0]["supporting_instance_ids"] == ["uuid"]
    else:
        output = projected["draft"]
        assert decode_phase_output(role, output, cases, items) == artifact
    assert output != decoded if role != "refiner" else True


def test_unknown_item_is_not_repaired():
    _, cases, items = phase_references("refiner", {"draft": {"checklist": [{"id": "plan-00009"}]}})
    with pytest.raises(ValueError, match="checklist.*unknown reference"):
        decode_phase_output("refiner", {"checklist": [{"id": "item-09"}]}, cases, items)


def test_branch_local_history_is_not_current_target_authority():
    history = {"contexts": [{"checklist": [{"id": "plan-00006", "condition": "old meaning"}]}],
               "events": [{"bullet_id": "plan-00006", "instance_id": "uuid", "rule_identity": "old"}]}
    payload = {"parent": {"checklist": [{"id": "plan-00006", "condition": "new meaning"}]},
               "records": [{"instance_id": "uuid"}], "history": history}
    projected, _, items = phase_references("curator", payload)
    assert projected["history"] == history
    assert projected["parent"]["checklist"][0]["id"] == "item-01"
    assert items.decode("item-01", "target") == "plan-00006"
    projected["history"]["events"][0]["rule_identity"] = "changed"
    assert payload["history"] == history


@pytest.mark.parametrize("role", ["reflector", "curator"])
def test_raw_scientific_objects_are_opaque_to_identity_projection(role):
    raw = {"id": "plan-00006", "instance_id": "uuid", "nested": [{"bullet_id": "plan-00006"}]}
    record = {"instance_id": "uuid", "pce_runs": [{"pce_output": raw}],
              "checker_input": raw, "checker_output": raw, "reflection_evidence": raw,
              "plan_trajectory": [raw], "code_trajectory": [raw]}
    artifact = {"checklist": [{"id": "plan-00006", "condition": "Keep uuid", "guide": "Keep plan-00006"}],
                "counters": [{"id": "plan-00006", "helpful": 1}]}
    payload = {"parent": artifact, **({"record": record} if role == "reflector" else {"records": [record]})}
    original = copy.deepcopy(payload)
    projected, _, _ = phase_references(role, payload)
    actual = projected["record"] if role == "reflector" else projected["records"][0]
    assert payload == original and actual["instance_id"] == "case-01"
    assert {key: value for key, value in actual.items() if key != "instance_id"} == {
        key: value for key, value in record.items() if key != "instance_id"}
    assert projected["parent"]["checklist"][0]["id"] == "item-01"
    assert projected["parent"]["counters"][0]["id"] == "item-01"


def test_credential_logging_cannot_silently_rewrite_learning_outputs(monkeypatch):
    from src.optimization.audit import redact_agent_record
    monkeypatch.setenv("TEST_ONLY_API_TOKEN", "synthetic-credential-never-real")
    original = {"agent_output": {"reasoning": "Keep the exact Plan", "operations": []},
                "metadata": {"api_key": "synthetic-credential-never-real"}}
    result = redact_agent_record(original)
    assert result["agent_output"] == original["agent_output"]
    assert result["metadata"]["api_key"] == "[REDACTED]"
    with pytest.raises(ValueError, match="refusing.*altered completion"):
        redact_agent_record({"agent_output": {"reasoning": "synthetic-credential-never-real"}})


@pytest.mark.parametrize("operation", [
    {"type": "ADD", "content": {"condition": "new", "guide": "inspect"}, "supporting_instance_ids": ["case-01"]},
    {"type": "UPDATE", "target_id": "item-01", "supporting_instance_ids": ["case-01"]},
    {"type": "MERGE", "target_ids": ["item-01", "item-02"], "supporting_instance_ids": ["case-01"]},
    {"type": "REMOVE", "target_id": "item-01", "supporting_instance_ids": []},
])
def test_all_operation_reference_transports(operation):
    _, cases, items = phase_references("curator", {"records": [{"instance_id": "uuid"}],
        "parent": {"checklist": [{"id": "plan-00006"}, {"id": "plan-00009"}]}})
    delta = {"operations": [operation]}
    before = copy.deepcopy(delta)
    decoded = decode_phase_output("curator", delta, cases, items)["operations"][0]
    assert delta == before
    assert decoded["supporting_instance_ids"] == (["uuid"] if operation["supporting_instance_ids"] else [])
    if "target_id" in operation:
        assert decoded["target_id"] == "plan-00006"
    if "target_ids" in operation:
        assert decoded["target_ids"] == ["plan-00006", "plan-00009"]
