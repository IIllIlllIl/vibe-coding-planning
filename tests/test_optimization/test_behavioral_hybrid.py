import json
from dataclasses import replace

import pytest

from src.optimization.behavioral_hybrid import (
    HybridLimits, HybridBehavioralAdapter,
    checker_bridge, validate_candidate,
)
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer
from src.optimization.behavioral_models import BehavioralGEPACase, BehavioralRepositoryProxy


LIMITS = HybridLimits(10, 80, 32, 80)
RAW = json.dumps({"schema_version": 1, "shared_guide": "Resolve claims against evidence.",
                  "checklist": [{"id": "plan-00001", "condition": "The Plan violates a material explicit requirement.",
                                 "guide": "Compare each material requirement with the proposed behavior."}]})


def case():
    return BehavioralGEPACase("case", "train", "ACCEPT", "high", "approved",
                             ({"role": "user", "content": "Task"},), "Plan",
                             BehavioralRepositoryProxy("repo", "abc", "case"),
                             {"future": "private supervision"}, {})


def test_complete_learning_interface():
    calls = []
    def checker(payload, artifact):
        assert "reflection_evidence" not in payload and "decision" not in payload
        calls.append("checker")
        return {"rule_results": [{"rule_number": 1, "triggered": False, "finding": None,
                                  "evidence": []}]}, [{"role": "assistant", "content": "investigation"}]
    def reflector(parent, record):
        assert record["checker_output"]["trajectory"]
        assert record["checker_input"]["proposed_plan_p1"] == "Plan"
        calls.append("reflector")
        return {"instance_id": record["instance_id"], **{key: "evidence" for key in
            ("reasoning", "error_identification", "root_cause_analysis", "correct_approach")}, "key_insights": [],
            "bullet_tags": [{"id": "plan-00001", "tag": "helpful", "attribution": "Evidence"}]}, [{"role": "assistant"}]
    def curator(parent, reviews, records, history, limits):
        calls.append("curator")
        return {"reasoning": "Keep", "operations": [], "shared_guide": parent["shared_guide"]}, [{"role": "assistant"}]
    proposer = IncrementalHybridACEProposer(reflector, curator, LIMITS)
    adapter = HybridBehavioralAdapter(checker_bridge(checker, LIMITS), limits=LIMITS, proposer=proposer)
    result = adapter.evaluate([case()], {"rules": RAW}, capture_traces=True)
    assert result.scores == [1.0]
    evidence = adapter.make_reflective_dataset({"rules": RAW}, result, ["rules"])
    assert json.loads(adapter.propose_new_texts({"rules": RAW}, evidence, ["rules"])["rules"]) == json.loads(RAW)
    assert calls == ["checker", "reflector", "curator"]
    assert proposer.last_trace["curator_trajectory"]
    with pytest.raises(ValueError, match="validation/test"):
        adapter.evaluate([replace(case(), split="validation")], {"rules": RAW}, True)
    assert adapter.evaluate([replace(case(), split="validation")], {"rules": RAW}).scores == [1.0]


def test_strict_limits_and_training_boundary():
    with pytest.raises(ValueError, match="exceeds"):
        validate_candidate(RAW, HybridLimits(10, 1, 32, 80))
    with pytest.raises(ValueError, match="duplicate JSON"):
        validate_candidate('{"schema_version":1,"schema_version":1}', LIMITS)
    proposer = IncrementalHybridACEProposer(lambda *args: None, lambda *args: None, LIMITS)
    with pytest.raises(ValueError, match="training"):
        proposer({"rules": RAW}, {"rules": [{"split": "validation"}]}, ["rules"])


def test_repeated_investigations_are_not_lost():
    def checker(payload, artifact):
        return {"rule_results": [{"rule_number": 1, "triggered": False, "finding": None, "evidence": []}]}, [
            {"role": "assistant", "content": "full trace"}]
    adapter = HybridBehavioralAdapter(checker_bridge(checker, LIMITS), limits=LIMITS,
                                      train_case_repetitions=3)
    result = adapter.evaluate([case()], {"rules": RAW}, True)
    physical = result.trajectories[0]["physical_investigations"]
    assert len(physical) == 3
    assert all(record["checker_output"]["trajectory"] for record in physical)
