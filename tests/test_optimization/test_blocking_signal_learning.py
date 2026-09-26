from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from src.optimization.models import PairedGEPACase, PairedPlanObservation, RepositoryRef
from src.optimization.paired_dataset import load_paired_snapshot
from src.optimization.playbook import PlaybookBullet, RejectPlaybook
from src.optimization.playbook_adapter import PairedRepoPlaybookGEPAAdapter
from src.optimization.playbook_hpc_agents import HPCPairedRepoPlaybookProposalAgents


ROOT = Path("configs/frozen_swe_verified_plan_pairs/20260926_blocking_signal_clean127_v1")
PROMPTS = Path("configs/prompts/offline_gepa_paired_binary_ace_codex_v5_blocking_signals_20260926.yaml")


def _case() -> PairedGEPACase:
    def observation(name: str) -> PairedPlanObservation:
        plan = f"# Plan\n{name}"
        return PairedPlanObservation(name, plan, hashlib.sha256(plan.encode()).hexdigest(), {})

    return PairedGEPACase(
        "pair-1", "org__repo-1", "train", "Fix behavior",
        RepositoryRef("org/repo", "base", "org__repo-1"),
        observation("R"), observation("U"),
    )


def _result(triggered: bool) -> dict:
    return {"rule_results": [{
        "rule_number": 1, "triggered": triggered,
        "finding": "Wrong behavior" if triggered else None,
        "evidence": [{"source": "plan", "location": None, "observation": "Wrong behavior"}]
        if triggered else [],
    }]}


def _evaluate(capture_traces: bool = True):
    r_trace = [{"role": "assistant", "content": "Inspect the existing contract"},
               {"role": "tool", "content": "full R observation\n" * 10000}]
    u_trace = [{"role": "assistant", "content": "Inspect the proposed branch"},
               {"role": "tool", "content": "U observation"}]

    class Checker:
        def evaluate_batch(self, batch, playbook):
            return [((_result(False), r_trace), (_result(True), u_trace)) for _ in batch]

    seed = RejectPlaybook((PlaybookBullet("plan-00001", "The Plan is a placeholder."),))
    adapter = PairedRepoPlaybookGEPAAdapter(Checker(), object(), checker_requires_reason=False)
    result = adapter.evaluate([_case()], {"rules": seed.serialize()}, capture_traces=capture_traces)
    return result, r_trace, u_trace


def test_complete_checker_trajectories_reach_reflector_files(tmp_path: Path) -> None:
    result, r_trace, u_trace = _evaluate()
    record = result.trajectories[0]
    original_record = json.loads(json.dumps(record))
    assert record["resolved_side"]["checker_trajectory"] == r_trace
    assert record["unresolved_side"]["checker_trajectory"] == u_trace
    assert "checker_trajectory" not in result.outputs[0]["resolved_side"]
    assert result.scores == [1.0]

    agents = HPCPairedRepoPlaybookProposalAgents(
        SimpleNamespace(run_dir=tmp_path), image_records={}, maximum_tokens=10000,
    )
    root = agents._write_pair_evidence(record, prior=None)
    manifest = json.loads((root / "manifest.json").read_text())
    for side, expected in (("resolved_side", r_trace), ("unresolved_side", u_trace)):
        name = f"{side}/checker_trajectory.json"
        assert name in manifest["files"]
        assert manifest["checker_trajectory_status"][side] == "available"
        assert json.loads((root / name).read_text()) == expected
    assert record == original_record
    assert agents._write_pair_evidence(record, prior=None) == root
    record["resolved_side"]["checker_trajectory"].append({"role": "tool", "content": "new fact"})
    assert agents._write_pair_evidence(record, prior=None) != root
    assert json.loads((root / "resolved_side/checker_trajectory.json").read_text()) == original_record["resolved_side"]["checker_trajectory"]


def test_metric_only_evaluation_does_not_expose_review_trajectories() -> None:
    result, _, _ = _evaluate(capture_traces=False)
    assert result.trajectories is None
    assert "checker_trajectory" not in json.dumps(result.outputs)


def test_legacy_missing_checker_trace_is_not_fabricated_or_overwritten(tmp_path: Path) -> None:
    result, _, _ = _evaluate()
    record = result.trajectories[0]
    del record["resolved_side"]["checker_trajectory"]
    record["unresolved_side"]["checker_trajectory"] = []
    old_identity = hashlib.sha256(json.dumps(
        {"record": record, "prior": None}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    old_root = tmp_path / "pair_reflection_evidence" / old_identity
    old_root.mkdir(parents=True)
    (old_root / "manifest.json").write_text("frozen old evidence")
    agents = HPCPairedRepoPlaybookProposalAgents(
        SimpleNamespace(run_dir=tmp_path), image_records={}, maximum_tokens=10000,
    )
    root = agents._write_pair_evidence(record, prior=None)
    assert root != old_root
    assert (old_root / "manifest.json").read_text() == "frozen old evidence"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["checker_trajectory_status"] == {
        "resolved_side": "unavailable_in_source_record",
        "unresolved_side": "recorded_empty",
    }
    assert not (root / "resolved_side/checker_trajectory.json").exists()
    assert json.loads((root / "unresolved_side/checker_trajectory.json").read_text()) == []


def test_clean127_selection_preserves_source_split_and_removes_all_bad_observation_pairs() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for name, fingerprint in manifest["artifacts"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == fingerprint
    selection = json.loads((ROOT / "selection.json").read_text())
    source = Path(selection["source_snapshot"])
    assert hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest() == selection["source_manifest_sha256"]
    train, validation = load_paired_snapshot(source)
    selected_train = [c for c in train if c.instance_id in selection["train_instance_ids"]]
    selected_validation = [c for c in validation if c.instance_id in selection["validation_instance_ids"]]
    assert [c.instance_id for c in selected_train] == selection["train_instance_ids"]
    assert len(selected_train) == 127
    assert len(selected_validation) == 36
    assert not {c.task_id for c in selected_train} & {c.task_id for c in selected_validation}
    previous = json.loads(Path("configs/frozen_swe_verified_plan_pairs/20260922_operationally_clean138_v1/selection.json").read_text())
    assert set(previous["train_instance_ids"]) - set(selection["train_instance_ids"]) == set(selection["excluded_from_previous_selection"])
    ledger = json.loads((ROOT / "exclusions.json").read_text())
    assert len(ledger) == 11
    assert {r["pair_id"] for r in ledger} == set(selection["excluded_from_previous_selection"])
    bad_obs = "e58f0d4dce3e9330af2092c08c0499ec5cf8be474c15375cf261f44f134538c6"
    assert all(c.unresolved_observation.observation_id != bad_obs for c in selected_train + selected_validation)
    audit = json.loads((ROOT / "observation_audit.json").read_text())
    assert len(audit) == 213
    assert all(r["artifact_sha256_verified"] and r["plan_sha256_verified"] for r in audit)


def test_blocking_signal_prompts_keep_checker_and_wire_schema_unchanged() -> None:
    prompts = yaml.safe_load(PROMPTS.read_text())
    old = yaml.safe_load(Path("configs/prompts/offline_gepa_paired_binary_ace_codex_v4_no_reason_20260925.yaml").read_text())
    for name in ("checker_system", "checker_instance"):
        assert prompts[name] == old[name]
    assert "checker_trajectory.json" in prompts["reflector_codex_instance"]
    assert "not a Plan that is excellent or complete" in prompts["reflector_codex_system"]
    assert '"concern":' in prompts["reflector_codex_instance"]
    curator = prompts["curator_codex_system"]
    assert all(op in curator for op in ("ADD", "UPDATE", "MERGE", "REMOVE"))
    assert "64 tokens" in curator
    assert "risk_analysis" not in json.dumps(prompts)
    assert "confidence" not in json.dumps(prompts)
