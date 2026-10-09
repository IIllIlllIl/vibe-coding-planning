"""No-model workflow, worker and cache tests for the opt-in hybrid path."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.optimization.behavioral_hybrid_runtime import run_hybrid_optimization, HybridPhaseExecutor
from src.optimization.behavioral_models import BehavioralCheckerOutput
from src.optimization.config import BehavioralHybridConfig, load_optimization_config
from src.optimization.behavioral_hybrid_ace import apply_hybrid_delta, _playbook
from src.optimization.behavioral_hybrid import HybridLimits, validate_candidate
from tests.test_optimization.test_behavioral_offline_foundation import _balanced_snapshot


LIMITS = HybridLimits(10, 200, 40, 80)
PARENT = {"schema_version": 1, "shared_guide": "Check claims against evidence", "checklist": [
    {"id": "plan-00001", "condition": "The Plan violates a material explicit requirement", "guide": "Compare proposed behavior with requirements"}]}


def review(parent, record):
    return {"instance_id": record["instance_id"], **{key: "Evidence" for key in
        ("reasoning", "error_identification", "root_cause_analysis", "correct_approach")}, "key_insights": [],
        "bullet_tags": [{"id": item["id"], "tag": "helpful", "attribution": "Observed contribution"}
                        for item in parent["checklist"]]}


def test_real_gepa_iteration_without_inference(tmp_path):
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(PARENT))
    config = replace(config, dataset_snapshot=_balanced_snapshot(tmp_path / "snapshot"),
        initial_rules_path=seed, run_dir=tmp_path / "run", execution=replace(config.execution, backend="local"),
        hybrid=BehavioralHybridConfig(enabled=True, prompt_bundle="configs/prompts/swe_chat_hybrid_ace_v1.yaml", evidence_sif_cache_dir=str(tmp_path / "sif")))
    class Phases:
        def call(self, role, payload):
            if role == "reflector":
                assert "checker_input" in payload["record"]
                assert payload["record"]["checker_output"]["trajectory"]
                return review(payload["parent"], payload["record"]), [{"role": "assistant", "content": "review"}]
            assert payload["history"]["events"]
            assert payload["parent"]["counters"][0]["helpful"] == 4
            return {"reasoning": "Improve shared evidence method", "operations": [],
                    "shared_guide": "Check claims against evidence and resolve conflicts independently"}, [{"role": "assistant"}]
    def checker(case, raw):
        assert "counters" not in json.loads(raw)
        return BehavioralCheckerOutput(case.accepted, "Synthetic decision", (), ({"role": "assistant", "content": "investigation"},))
    result = run_hybrid_optimization(config, checker=checker, phase_executor=Phases())
    assert result is not None
    assert (config.run_dir / "gepa_state.bin").exists()
    assert (config.run_dir / "global_counter_ledger.json").exists()
    traces = list((config.run_dir / "hybrid_proposals").glob("*.json"))
    assert json.loads(traces[0].read_text())["status"] == "completed"


def test_limits_operations_and_guide_identity():
    records = [{"instance_id": "train"}]
    delta = {"reasoning": "Generalize", "shared_guide": PARENT["shared_guide"], "operations": [
        {"type": "UPDATE", "target_id": "plan-00001", "content": {"condition": "The Plan violates a material explicit requirement",
          "guide": "Trace each material constraint to its proposed implementation"}, "supporting_instance_ids": ["train"]}]}
    proposed = json.loads(apply_hybrid_delta(PARENT, delta, records, LIMITS))
    assert proposed["checklist"][0]["id"] != "plan-00001"
    assert _playbook(proposed).bullets[0].helpful == 0
    same_id_changed_guide = dict(PARENT, shared_guide="Compare requirements independently")
    assert _playbook(PARENT).bullets[0].text != _playbook(same_id_changed_guide).bullets[0].text
    delta["shared_guide"] = " ".join(["word"] * 201)
    with pytest.raises(ValueError, match="200"):
        apply_hybrid_delta(PARENT, delta, records, LIMITS)


def test_hybrid_script_reuses_shared_submit_contract(tmp_path):
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    config = replace(config, run_dir=tmp_path / "run")
    executor = HybridPhaseExecutor(config)
    script = executor._script(tmp_path / "task", 1, phase="hybrid-curator",
        worker_module_override="src.optimization.behavioral_hybrid_worker", load_container_modules=True)
    assert "src.optimization.behavioral_hybrid_worker" in script
    assert "#SBATCH --array=0" in script
    assert "#SBATCH --cpus-per-task=1" in script
    assert "module load tools/Apptainer" in script


@pytest.mark.parametrize("component,cap", [("shared_guide", 200), ("condition", 40), ("guide", 80)])
def test_each_word_limit_is_inclusive(component, cap):
    artifact = json.loads(json.dumps(PARENT))
    target = artifact if component == "shared_guide" else artifact["checklist"][0]
    target[component] = " ".join(["word"] * cap)
    validate_candidate(json.dumps(artifact), LIMITS)
    target[component] += " extra"
    with pytest.raises(ValueError, match="exceeds"):
        validate_candidate(json.dumps(artifact), LIMITS)


def test_all_four_operations_and_shared_rewrite():
    records = [{"instance_id": "case"}]
    def apply(parent, ops):
        return json.loads(apply_hybrid_delta(parent, {"reasoning": "Evidence-based maintenance",
            "shared_guide": "Resolve factual conflicts independently", "operations": ops}, records, LIMITS))
    added = apply(PARENT, [{"type": "ADD", "content": {"condition": "The Plan breaks an important existing boundary",
        "guide": "Inspect relevant boundary tests"}, "supporting_instance_ids": ["case"]}])
    assert len(added["checklist"]) == 2
    merged = apply(added, [{"type": "MERGE", "target_ids": [item["id"] for item in added["checklist"]],
        "content": {"condition": "The Plan violates a material required behavior", "guide": "Compare new and preserved requirements"},
        "supporting_instance_ids": ["case"]}])
    assert len(merged["checklist"]) == 1
    removed = apply(added, [{"type": "REMOVE", "target_id": added["checklist"][1]["id"], "supporting_instance_ids": ["case"]}])
    assert removed["checklist"] == PARENT["checklist"]


def test_invalid_completion_is_retained_before_validation(tmp_path, monkeypatch):
    import src.optimization.behavioral_hybrid_runtime as runtime
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    config = replace(config, hybrid=BehavioralHybridConfig(enabled=True,
        prompt_bundle="configs/prompts/swe_chat_hybrid_ace_v1.yaml", evidence_sif_cache_dir=str(tmp_path / "sif")))
    def invalid(**kwargs):
        assert "/tmp/curator.json" in kwargs["instance_template"]
        return {"wrong": "raw completion"}, [{"role": "assistant", "content": "original"}]
    monkeypatch.setattr(runtime, "run_evidence_curator", invalid)
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda config: None)
    attempt = tmp_path / "attempt"
    with pytest.raises(ValueError, match="Curator fields"):
        runtime.run_local_phase(config, "curator", {"parent": PARENT, "records": [{"split": "train", "instance_id": "case"}]}, attempt)
    assert json.loads((attempt / "completion.json").read_text())["agent_output"] == {"wrong": "raw completion"}


def test_worker_boundary_and_completed_output(tmp_path, monkeypatch):
    import src.optimization.behavioral_hybrid_worker as worker
    from src.optimization.behavioral_hybrid_runtime import phase_identity
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    config = replace(config, hybrid=BehavioralHybridConfig(enabled=True))
    payload = {"parent": PARENT, "record": {"instance_id": "case", "split": "train"}}
    manifest = {"mode": "behavioral_hybrid_phase", "role": "reflector", "payload": payload,
                "fingerprint": phase_identity(config, "reflector", payload)}
    path, output = tmp_path / "input.json", tmp_path / "result.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(worker, "load_optimization_config", lambda path: config)
    monkeypatch.setattr(worker, "run_local_phase", lambda *args, **kwargs: (review(PARENT, payload["record"]), [{"role": "assistant"}]))
    args = dict(config_path=Path("unused"), manifest_path=path, output_path=output, attempt_dir=tmp_path / "attempt")
    assert worker.run_task(**args) == 0
    assert json.loads(output.read_text())["status"] == "completed"
    manifest["payload"]["record"]["split"] = "validation"
    manifest["fingerprint"] = phase_identity(config, "reflector", manifest["payload"])
    path.write_text(json.dumps(manifest))
    assert worker.run_task(**args) == 1
    assert "only training" in json.loads(output.read_text())["error"]


def test_hybrid_config_limits_and_prompt_hash(tmp_path):
    import yaml
    raw = yaml.safe_load(Path("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml").read_text())
    raw["hybrid"] = {"enabled": True, "prompt_bundle": "configs/prompts/swe_chat_hybrid_ace_v1.yaml",
                     "evidence_sif_cache_dir": str(tmp_path / "sif")}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_optimization_config(path, require_api_keys=False)
    assert (config.hybrid.shared_guide_words, config.hybrid.condition_words, config.hybrid.item_guide_words) == (200, 40, 80)
    assert len(config.hybrid.prompt_bundle_sha256) == 64
    assert "rule_results" in config.checker_prompt
    assert "candidate_guideline" in config.checker_instance_template


def test_missing_prepared_sif_fails_without_inference(tmp_path):
    from src.optimization.behavioral_hybrid_runtime import require_evidence_sif
    config = load_optimization_config("configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml", require_api_keys=False)
    config = replace(config, hybrid=BehavioralHybridConfig(enabled=True))
    with pytest.raises(ValueError, match="frozen evidence_sif"):
        require_evidence_sif(config)


def test_history_only_remove_does_not_invent_supporting_ids():
    parent = json.loads(json.dumps(PARENT))
    parent["checklist"].append({"id": "plan-00002", "condition": "A distinct obsolete condition", "guide": "Inspect its evidence"})
    delta = {"reasoning": "Accumulated history no longer supports this condition", "shared_guide": parent["shared_guide"],
             "operations": [{"type": "REMOVE", "target_id": "plan-00002", "supporting_instance_ids": []}]}
    with pytest.raises(ValueError, match="supporting IDs"):
        apply_hybrid_delta(parent, delta, [{"instance_id": "case"}], LIMITS)
    result = json.loads(apply_hybrid_delta(parent, delta, [{"instance_id": "case"}], LIMITS,
                                         history={"events": {"prior": {"tag": "harmful"}}}))
    assert len(result["checklist"]) == 1
    assert delta["operations"][0]["supporting_instance_ids"] == []


def test_changed_guides_keep_history_but_not_stale_counters():
    from src.optimization.playbook_adapter import GlobalPlaybookCounters
    ledger = GlobalPlaybookCounters()
    original = _playbook(PARENT)
    record = {"instance_id": "case", "split": "train"}
    original_review = review(PARENT, record)
    counted, pending = ledger.apply_reviews(original, [original_review])
    history = ledger.feedback_for(original, [original_review], [record], include_inactive=True)
    ledger.commit(counted, pending, feedback=history)
    changed = dict(PARENT, shared_guide="A different investigation strategy")
    assert ledger.hydrate(_playbook(changed)).bullets[0].helpful == 0
    retained = ledger.feedback_for(_playbook(changed), include_inactive=True)
    assert retained["events"] and retained["reports"]
