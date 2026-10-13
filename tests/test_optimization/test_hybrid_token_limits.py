import json
import pytest
from src.optimization.behavioral_hybrid import HybridLimits, HybridSizeLimitError, validate_candidate


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


def test_effective_limits_have_one_unambiguous_unit_per_field():
    caps = HybridLimits(10, 200, 40, 80, 512, 48, 96)
    assert caps.agent_view() == {"max_items": 10, "shared_guide_tokens": 512,
        "condition_tokens": 48, "item_guide_tokens": 96,
        "token_counter_model": "deepseek/deepseek-chat"}
    assert HybridLimits(10, 200, 40, 80).agent_view() == {"max_items": 10,
        "shared_guide_words": 200, "condition_words": 40, "item_guide_words": 80}


def test_all_size_violations_have_paths_units_and_counts(monkeypatch):
    import litellm
    monkeypatch.setattr(litellm, "token_counter", lambda **kw: len(kw["text"]))
    artifact = {"schema_version": 1, "shared_guide": "abcd", "checklist": [
        {"id": "plan-00001", "condition": "abcd", "guide": "abcde"},
        {"id": "plan-00002", "condition": "abc", "guide": "abcdef"}]}
    before = json.dumps(artifact)
    with pytest.raises(HybridSizeLimitError) as raised:
        validate_candidate(before, HybridLimits(1, 1, 1, 1, 3, 3, 3))
    assert [row["path"] for row in raised.value.violations] == [
        "shared_guide", "checklist", "checklist[0].condition", "checklist[0].guide", "checklist[1].guide"]
    assert raised.value.violations[-1] == {"path": "checklist[1].guide", "actual": 6, "limit": 3, "unit": "tokens"}
    assert "checklist[0].guide exceeds 3 tokens (received 5)" in str(raised.value)
    assert json.dumps(artifact) == before


def test_real_failed_smoke_text_uses_tokens_and_fits_relaxed_cap():
    guide = ("Map the requested deliverable and workflow to the Plan's proposed behavior, coverage, interfaces, "
             "and explicit exclusions. Check relevant prior proposals and the user's assent when they define "
             "the request. Trigger when the Plan drops a central requested path, substitutes a narrower "
             "deliverable, or proposes user-facing names or command relationships that conflict with stated "
             "intent. Do not require wording revealed only after the Plan boundary or reject a reasonable "
             "choice where the request remains genuinely open.")
    artifact = {"schema_version": 1, "shared_guide": "Check the task", "checklist": [
        {"id": "plan-00001", "condition": "Check requested scope", "guide": guide}]}
    assert len(guide.split()) == 75
    with pytest.raises(HybridSizeLimitError) as raised:
        validate_candidate(json.dumps(artifact), HybridLimits(10, 200, 40, 80, 256, 32, 64))
    assert raised.value.violations[0]["unit"] == "tokens"
    assert raised.value.violations[0]["actual"] > 64
    assert validate_candidate(json.dumps(artifact), HybridLimits(10, 200, 40, 80, 512, 48, 96)) == artifact


@pytest.mark.parametrize("path", [
    "configs/gepa_swe_chat_hybrid_reference_token_smoke_v2_20261013.yaml",
    "configs/gepa_swe_chat_hybrid_formal3_v8_20261013.yaml",
])
def test_relaxed_token_configs_preserve_science_and_distinct_paths(path):
    from pathlib import Path
    import hashlib
    import yaml
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hybrid_runtime import hybrid_limits, verify_hybrid_snapshot
    cfg = load_optimization_config(path, require_api_keys=False)
    caps = hybrid_limits(cfg)
    assert (caps.shared_guide_tokens, caps.condition_tokens, caps.item_guide_tokens) == (512, 48, 96)
    validate_candidate(cfg.initial_rules_path.read_text(), caps)
    verify_hybrid_snapshot(cfg)
    raw = yaml.safe_load(Path(path).read_text())
    contract = raw.get("experiment_contract", raw.get("smoke_contract"))
    assert contract["launch_authorized"] is ("smoke" in path)
    assert cfg.run_dir.name == contract["identity"].removeprefix("swe-chat-")
    assert raw["hpc"]["worker_config_path"] == path
    assert cfg.hybrid.max_items == 10
    assert cfg.search.reflection_minibatch_size == (4 if "smoke" in path else 16)
    assert cfg.hpc.mem == "4G" and cfg.hpc.cpus_per_task == 1
    assert cfg.hybrid.checker_model == {}
    assert contract["prompt_bundle_sha256"] == hashlib.sha256(Path(cfg.hybrid.prompt_bundle).read_bytes()).hexdigest()


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
