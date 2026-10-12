import json
import pytest
from src.optimization.behavioral_hybrid import HybridLimits, validate_candidate


def test_token_boundary_and_legacy_word_mode(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "token_counter", lambda **kw: len(kw["text"]))
    artifact = {"schema_version": 1, "shared_guide": "abc", "checklist": [
        {"id": "plan-00001", "condition": "abc", "guide": "abc"}]}
    caps = HybridLimits(10, 1, 1, 1, 3, 3, 3)
    assert validate_candidate(json.dumps(artifact), caps) == artifact
    for field in ("shared_guide", "condition", "guide"):
        changed = json.loads(json.dumps(artifact))
        (changed if field == "shared_guide" else changed["checklist"][0])[field] = "abcd"
        with pytest.raises(ValueError, match="exceeds 3 tokens"):
            validate_candidate(json.dumps(changed), caps)
    assert validate_candidate(json.dumps(artifact), HybridLimits(10, 1, 1, 1)) == artifact


def test_requested_token_budget():
    caps = HybridLimits(10, 200, 40, 80, 256, 32, 64)
    assert (caps.shared_guide_tokens, caps.condition_tokens, caps.item_guide_tokens) == (256, 32, 64)
