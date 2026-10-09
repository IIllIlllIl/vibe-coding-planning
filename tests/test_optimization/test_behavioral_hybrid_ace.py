import json

import pytest

from src.optimization.behavioral_hybrid import HybridLimits
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer


def test_incremental_history_resume_and_failed_transaction(tmp_path):
    limits = HybridLimits(10, 80, 32, 80)
    candidate = {"rules": json.dumps({"schema_version": 1, "shared_guide": "Check evidence",
        "checklist": [{"id": "plan-00001", "condition": "Material constraint violated", "guide": "Compare requirements"}]})}
    def reflect(parent, record):
        return {"instance_id": record["instance_id"], **{key: "evidence" for key in
            ("reasoning", "error_identification", "root_cause_analysis", "correct_approach")}, "key_insights": [],
            "bullet_tags": [{"id": "plan-00001", "tag": "helpful", "attribution": "Explicit conflict"}]}, [{"content": "full investigation"}]
    def curate(parent, reviews, records, history, caps):
        assert parent["counters"][0]["helpful"] == 1
        assert history["events"] and history["reports"]
        return {"reasoning": "Preserve", "shared_guide": parent["shared_guide"], "operations": []}, []
    path = tmp_path / "ledger.json"
    records = {"rules": [{"instance_id": "case", "split": "train"}]}
    proposer = IncrementalHybridACEProposer(reflect, curate, limits, ledger_path=path)
    result = proposer(candidate, records, ["rules"])
    assert json.loads(result["rules"]) == json.loads(candidate["rules"])
    replay = IncrementalHybridACEProposer(reflect, curate, limits, ledger_path=path)
    replay(candidate, records, ["rules"])
    before = path.read_bytes()
    replay.curator = lambda *args: ({"reasoning": "bad", "shared_guide": "", "operations": []}, [])
    with pytest.raises(ValueError):
        replay(candidate, records, ["rules"])
    assert path.read_bytes() == before
