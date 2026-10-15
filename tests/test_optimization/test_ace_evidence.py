"""Formal ACE layout reuse and large-minibatch evidence coverage, no inference."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from src.optimization.ace_evidence import write_curator_evidence
from src.optimization.agent_references import phase_references
from src.optimization.behavioral_hybrid_ace import _playbook
from src.optimization.behavioral_hybrid_evidence import write_hybrid_evidence
from src.optimization.playbook_adapter import GlobalPlaybookCounters
from tests.test_optimization.test_behavioral_hybrid_runtime import PARENT, review


def read(root, path):
    return json.loads((root / path).read_text())


def payload(count):
    rows = [{"instance_id": f"long-dataset-id-{number}", "split": "train",
             "observed_accept": bool(number % 2),
             "observed_decision": "ACCEPT" if number % 2 else "DO_NOT_ACCEPT",
             "checker_input": {"plan": f"Original Plan {number}"},
             "checker_output": {"trajectory": [{"content": "raw tool observation " * 10000}]},
             "reflection_evidence": {"pre_plan": "Recorded evidence"}}
            for number in range(count)]
    reports = [dict(review(PARENT, row),
                    reasoning=f"Complete diagnosis {number}",
                    root_cause_analysis=f"Root cause {number}",
                    key_insights=[{"failure_pattern": f"Insight {number}",
                                   "basis": f"Opposing and supporting evidence {number}"}])
               for number, row in enumerate(rows)]
    ledger = GlobalPlaybookCounters()
    counted, _ = ledger.apply_reviews(_playbook(PARENT), reports)
    history = ledger.feedback_for(counted, reports, rows, include_inactive=True)
    parent = dict(PARENT, counters=[{"id": bullet.id, "helpful": bullet.helpful,
                  "harmful": bullet.harmful, "neutral": bullet.neutral} for bullet in counted.bullets])
    return {"parent": parent, "records": rows, "reviews": reports,
            "history": history, "limits": {"max_items": 10, "shared_guide_tokens": 512,
                                           "condition_tokens": 48, "item_guide_tokens": 96}}


@pytest.mark.parametrize("count", [16, 24])
def test_large_minibatch_keeps_complete_reflections_in_small_required_core(tmp_path, count):
    original = payload(count)
    before = copy.deepcopy(original)
    projected, _, _ = phase_references("curator", original)
    manifest = write_hybrid_evidence(tmp_path, "curator", projected,
                                     canonical_parent=original["parent"])
    assert original == before
    assert not (tmp_path / "input.json").exists()
    assert {"counted_playbook.json", "reflection_index.json", "records.json",
            "rule_feedback_history_index.json", "limits.json"} <= set(manifest["required_files"])
    assert not set(manifest["required_files"]) & set(manifest["optional_files"])
    assert set(manifest["files"]) == set(manifest["required_files"] + manifest["optional_files"])
    assert all((tmp_path / path).is_file() for path in manifest["files"])
    # The multi-MB raw trajectories must not swell the mandatory learning core.
    assert sum((tmp_path / path).stat().st_size for path in manifest["files"]) > 1_000_000
    assert sum((tmp_path / path).stat().st_size for path in manifest["required_files"]) < 128_000
    indexed = read(tmp_path, "reflection_index.json")
    assert len(indexed) == count
    for source, entry in zip(projected["reviews"], indexed, strict=True):
        restored = dict(entry, key_insights=[{k: v for k, v in insight.items() if k != "id"}
                                            for insight in entry["key_insights"]])
        assert restored == source  # Includes bases, full diagnoses and tag reasoning.
    assert read(tmp_path, "case_reflections.json") == projected["reviews"]
    assert read(tmp_path, "rule_feedback_history.json") == original["history"]
    for source, entry in zip(projected["records"], read(tmp_path, "records.json"), strict=True):
        assert entry["observed_accept"] == source["observed_accept"]
        assert {key: read(tmp_path, path) for key, path in entry["fields"].items()} == source
        assert set(entry["fields"].values()) <= set(manifest["optional_files"])
    index = read(tmp_path, "rule_feedback_history_index.json")["bullets"][0]
    assert index["bullet_id"] == "item-01"
    assert index["counts"]["helpful"] == count
    assert index["history_observations"]["helpful"] == count
    assert index["rule_identity"] == " ".join(_playbook(PARENT).bullets[0].text.casefold().split())


def test_hybrid_and_formal_share_identical_ace_core(tmp_path):
    original = payload(2)
    projected, _, _ = phase_references("curator", original)
    hybrid = tmp_path / "hybrid"
    write_hybrid_evidence(hybrid, "curator", projected, canonical_parent=original["parent"])
    formal = tmp_path / "formal"
    bullets = [replace(b, id="item-01", helpful=2) for b in _playbook(PARENT).bullets]
    write_curator_evidence(formal, projected["parent"], bullets, projected["reviews"],
                           feedback_history=original["history"])
    baseline_manifest = read(formal, "manifest.json")
    assert baseline_manifest == {
        "schema_version": 1, "case_count": 2,
        "files": ["counted_playbook.json", "reflection_index.json", "case_reflections.json",
                  "rule_feedback_history_index.json", "rule_feedback_history.json"],
        "required_files": ["counted_playbook.json", "reflection_index.json",
                           "rule_feedback_history_index.json"],
        "optional_files": ["case_reflections.json", "rule_feedback_history.json"],
        "contains_repository": False, "contains_direct_downstream_evidence": False,
    }
    for path in baseline_manifest["files"]:
        assert (hybrid / path).read_bytes() == (formal / path).read_bytes()


def test_reflector_has_full_investigation_and_refiner_has_separate_inputs(tmp_path):
    original = payload(1)
    projected, _, _ = phase_references("reflector", {
        "parent": original["parent"], "record": original["records"][0]})
    root = tmp_path / "reflector"
    manifest = write_hybrid_evidence(root, "reflector", projected)
    entry = read(root, "record.json")
    assert set(entry["fields"].values()) <= set(manifest["required_files"])
    assert read(root, entry["fields"]["checker_output"]) == projected["record"]["checker_output"]
    refined = {"draft": projected["parent"], "limits": original["limits"],
               "split": "train", "size_violations": [{"path": "shared_guide", "actual": 513}]}
    root = tmp_path / "refiner"
    manifest = write_hybrid_evidence(root, "refiner", refined)
    assert {path[:-5]: read(root, path) for path in manifest["required_files"]} == refined


def test_new_prompt_changes_only_file_reading_contract():
    old = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v5.yaml").read_text())
    new = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v6.yaml").read_text())
    for role in ("checker_task", "checker_instance", "reflector_task", "refiner_task"):
        assert new[role] == old[role]
    old_task, new_task = old["curator_task"], new["curator_task"]
    assert "input.json" not in new_task
    assert old_task.split("## Evidence and artifact maintenance")[0] == new_task.split("## Evidence and artifact maintenance")[0]
    assert old_task.split("## Condition and guide writing")[1] == new_task.split("## Condition and guide writing")[1].replace("from records.json entries' instance_id;", "from records[].instance_id;")


def test_production_curator_reads_large_batch_through_shared_bundle(tmp_path, monkeypatch):
    import src.optimization.behavioral_hybrid_runtime as runtime
    from src.optimization.config import load_optimization_config
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_formal3_v9_20261015.yaml",
                                      require_api_keys=False)
    original = payload(16)
    inspected = []
    def agent(**kwargs):
        root = Path(kwargs["evidence_dir"])
        manifest = read(root, "manifest.json")
        for path in manifest["required_files"]:
            inspected.append((path, read(root, path)))
        reports = dict(inspected)["reflection_index.json"]
        assert len(reports) == 16
        assert reports[-1]["key_insights"][0]["basis"] == "Opposing and supporting evidence 15"
        assert "input.json" not in kwargs["system"]
        assert "required_files" in kwargs["instance_template"]
        assert "Read required evidence in manageable sections" in kwargs["instance_template"]
        return {"reasoning": "Retain after reviewing all evidence", "operations": [],
                "shared_guide": original["parent"]["shared_guide"]}, []
    monkeypatch.setattr(runtime, "require_evidence_sif", lambda *_: None)
    monkeypatch.setattr(runtime, "run_evidence_curator", agent)
    output, _ = runtime.run_local_phase(config, "curator", original, tmp_path / "attempt")
    assert output["operations"] == []
    assert (tmp_path / "attempt/completion.json").is_file()
    assert len(inspected) == 5


def test_repaired_config_preserves_experiment_design_and_freezes_new_prompt():
    import hashlib
    old_path = Path("configs/gepa_swe_chat_hybrid_formal3_v8_20261013.yaml")
    new_path = Path("configs/gepa_swe_chat_hybrid_formal3_v9_20261015.yaml")
    old = yaml.safe_load(old_path.read_text().replace("formal3-v8-20261013", "formal3-v9-20261015")
                         .replace("formal3_v8_20261013", "formal3_v9_20261015"))
    new = yaml.safe_load(new_path.read_text())
    for key in ("task", "behavioral_repository", "checker", "reflection", "search",
                "docker", "container", "execution", "hpc", "paths", "prompts"):
        assert new[key] == old[key]
    before, after = dict(old["hybrid"]), dict(new["hybrid"])
    before.pop("prompt_bundle")
    after.pop("prompt_bundle")
    assert before == after
    assert new["experiment_contract"]["launch_authorized"] is False
    assert new["experiment_contract"]["prompt_bundle_sha256"] == hashlib.sha256(
        Path(new["hybrid"]["prompt_bundle"]).read_bytes()).hexdigest()
    launch = yaml.safe_load(Path("configs/swe_chat_hybrid_formal3_supervisor_v9_20261015.yaml").read_text())
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(new_path)
    assert args[args.index("--job-name") + 1] == new["experiment_contract"]["identity"]
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == new["hpc"]["mem"] == "4G"
    assert "--require-clean-worktree" in args
    assert "--reclaim-staging" in args
    assert "--remote-project-dir" not in args
