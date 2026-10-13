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


@pytest.mark.parametrize("path,mini,iterations,projection", [
    ("configs/gepa_swe_chat_hybrid_reference_token_smoke_v1_20261013.yaml", 4, 1, 16),
    ("configs/gepa_swe_chat_hybrid_formal3_v7_20261013.yaml", 16, 3, 188),
])
def test_new_launch_caps_prompt_and_baseline(path, mini, iterations, projection):
    from pathlib import Path
    import hashlib
    import yaml
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hybrid_runtime import hybrid_limits
    config = load_optimization_config(path, require_api_keys=False)
    caps = hybrid_limits(config)
    assert (caps.shared_guide_tokens, caps.condition_tokens, caps.item_guide_tokens) == (256, 32, 64)
    assert config.search.reflection_minibatch_size == mini
    assert config.search.max_iterations == iterations
    assert config.search.projection_metric_calls == projection
    assert config.hpc.cpus_per_task == 1 and config.hpc.mem == "4G"
    assert config.hybrid.checker_model == {}
    assert config.hybrid.refiner_enabled
    raw = yaml.safe_load(Path(path).read_text())
    contract = raw.get("experiment_contract", raw.get("smoke_contract"))
    assert config.run_dir.name == contract["identity"].removeprefix("swe-chat-")
    assert raw["hpc"]["worker_config_path"] == path
    assert raw["hpc"]["remote_task_dir"].endswith(contract["identity"])
    assert raw["behavioral_repository"]["workspace_root"].endswith(contract["identity"])
    assert contract["prompt_bundle_sha256"] == hashlib.sha256(Path(config.hybrid.prompt_bundle).read_bytes()).hexdigest()
    assert str(config.hybrid.prompt_bundle).endswith("swe_chat_hybrid_ace_v4.yaml")
