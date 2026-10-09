import json
from pathlib import Path
from types import SimpleNamespace

from src.optimization.behavioral_hybrid import HybridBehavioralAdapter, HybridLimits, validate_candidate
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer


def test_seed_scores_and_invalid_gate():
    limits = HybridLimits(10, 200, 40, 80)
    seed = Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text()
    validate_candidate(seed, limits)
    adapter = HybridBehavioralAdapter(lambda *args: None, limits=limits)
    assert adapter._score(SimpleNamespace(accepted=True), True) == 1
    assert adapter._score(SimpleNamespace(accepted=True), False) == -1
    assert adapter._score(SimpleNamespace(accepted=False), True) == 0
    assert adapter._score(SimpleNamespace(accepted=False), False) == 1
    assert adapter.primary_metric == "human_decision_utility"
    assert adapter.evaluate([SimpleNamespace()], {"rules": "{}"}).scores == [-100]


def test_refiner_after_curator_only_for_size():
    limits = HybridLimits(10, 200, 40, 80)
    seed = Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text()
    calls = []
    def reflect(parent, record):
        return {"instance_id": "case", "reasoning": "Observed", "error_identification": None,
                "root_cause_analysis": None, "correct_approach": None, "key_insights": [],
                "bullet_tags": [{"id": "plan-00001", "tag": "neutral", "attribution": None}]}, []
    def curate(*args):
        calls.append("curator")
        return {"reasoning": "Revise guide", "operations": [], "shared_guide": "word " * 201}, []
    def refine(draft, caps):
        calls.append("refiner")
        draft["shared_guide"] = "Judge material concerns."
        return draft, []
    proposer = IncrementalHybridACEProposer(reflect, curate, limits, refiner=refine)
    output = proposer({"rules": seed}, {"rules": [{"instance_id": "case", "split": "train"}]}, ["rules"])
    validate_candidate(output["rules"], limits)
    assert calls == ["curator", "refiner"]


def test_smoke_config_and_native_checker_script(tmp_path, monkeypatch):
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_smoke_v1_20261009.yaml")
    assert config.hybrid.checker_model["executor"] == "codex_cli"
    assert config.search.max_iterations == 1
    assert config.hybrid.refiner_enabled
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path,
                                                   task_indices=[0], attempt=1)
    assert "module load tools/Apptainer" in script
    assert 'test -n "${DEEPSEEK_API_KEY:-}"' not in script
