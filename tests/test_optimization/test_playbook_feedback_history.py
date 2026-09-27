"""Training-only Ref history, resume idempotency and hidden metadata."""

import json

import pytest

from src.optimization.playbook import (
    PlaybookBullet, RejectPlaybook, validate_curator_proposal,
)
from src.optimization.playbook_adapter import GlobalPlaybookCounters, TwoStagePlaybookProposer


def seed():
    return RejectPlaybook((PlaybookBullet("plan-00001", "The Plan is a placeholder."),))


def review(case, tag, explanation="Observed training evidence."):
    return {
        "instance_id": case,
        "reasoning": "Training pair comparison.",
        "error_identification": "Error.",
        "root_cause_analysis": "Cause.",
        "correct_approach": "Approach.",
        "key_insight": "Insight.",
        "bullet_tags": [{"id": "plan-00001", "tag": tag,
                         "attribution": explanation, "confidence": "high"}],
        "uncertainty": "Proxy limitation.",
    }


def apply(ledger, book, reviews, records):
    parent = ledger.hydrate(book)
    counted, pending = ledger.apply_reviews(parent, reviews)
    history = ledger.feedback_for(parent, reviews, records)
    ledger.commit(counted, pending, feedback=history)
    return counted, history


def test_three_tags_and_history_survive_resume(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = GlobalPlaybookCounters(path)
    reviews = [review("train-a", "helpful"), review("train-b", "harmful"), review("train-c", "neutral")]
    records = [{"instance_id": value["instance_id"]} for value in reviews]
    counted, history = apply(ledger, seed(), reviews, records)
    assert (counted.bullets[0].helpful, counted.bullets[0].harmful, counted.bullets[0].neutral) == (1, 1, 1)
    assert len(history["events"]) == 3
    assert len(history["reports"]) == 3
    assert all(event["reflection_sha256"] in history["reports"] for event in history["events"].values())
    resumed = GlobalPlaybookCounters(path)
    again, second = apply(resumed, seed(), reviews, records)
    assert again == counted
    assert second == history
    visible = counted.render_for_checker()
    assert "plan-00001" not in visible
    assert "helpful" not in visible and "neutral" not in visible
    assert RejectPlaybook.parse(counted.serialize()) == counted


def test_new_context_keeps_changed_feedback_without_recounting(tmp_path):
    ledger = GlobalPlaybookCounters(tmp_path / "ledger.json")
    record = {"instance_id": "train-a"}
    apply(ledger, seed(), [review("train-a", "harmful")], [record])
    expanded = RejectPlaybook((*seed().bullets, PlaybookBullet("plan-00002", "A different condition.")))
    counted, history = apply(ledger, expanded, [review("train-a", "helpful", "Changed application in a new checklist.")], [record])
    assert (counted.bullets[0].helpful, counted.bullets[0].harmful) == (0, 1)
    assert len(history["events"]) == 2
    assert len(history["contexts"]) == 2
    assert {event["tag"] for event in history["events"].values()} == {"helpful", "harmful"}


def test_duplicate_case_in_one_batch_does_not_increment_twice():
    ledger = GlobalPlaybookCounters()
    reviews = [review("train-a", "helpful")] * 2
    counted, history = apply(ledger, seed(), reviews, [{"instance_id": "train-a"}] * 2)
    assert counted.bullets[0].helpful == 1
    assert len(history["events"]) == 1


def test_neutral_does_not_block_first_non_neutral_attribution():
    ledger = GlobalPlaybookCounters()
    record = {"instance_id": "train-a"}
    apply(ledger, seed(), [review("train-a", "neutral")], [record])
    counted, history = apply(ledger, seed(), [review("train-a", "helpful")], [record])
    assert (counted.bullets[0].helpful, counted.bullets[0].neutral) == (1, 1)
    assert len(history["events"]) == 2


def test_load_old_ledger_without_inventing_explanations(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"schema_version": 1,
        "counts": {"the plan is a placeholder.": {"helpful": 2, "harmful": 1}},
        "observations": {"the plan is a placeholder.": {"train-a": "helpful"}},
    }))
    ledger = GlobalPlaybookCounters(path)
    bullet = ledger.hydrate(seed()).bullets[0]
    assert (bullet.helpful, bullet.harmful, bullet.neutral) == (2, 1, 0)
    assert ledger.feedback_for(seed())["events"] == {}


def test_valid_proposal_exposes_ref_history_and_survives_branch_rejection(tmp_path):
    seen = []
    proposer = TwoStagePlaybookProposer(
        reflector=lambda record: review(record["instance_id"], record["tag"]),
        curator=lambda *_: pytest.fail("history-aware curator should run"),
        curator_with_history=lambda counted, reviews, records, history: (
            seen.append(history) or {"reasoning": "Retain.", "operations": []}
        ),
        token_counter=lambda text: len(text.split()),
        global_counter_path=tmp_path / "ledger.json",
    )
    # GEPA can reject the returned candidate; global training evidence is
    # already committed and survives a later sibling proposal from the Seed.
    for case, tag in [("train-a", "harmful"), ("train-b", "neutral")]:
        proposer({"rules": seed().serialize()},
                 {"rules": [{"instance_id": case, "tag": tag}]}, ["rules"])
    assert len(seen[1]["events"]) == 2
    assert {event["instance_id"] for event in seen[1]["events"].values()} == {"train-a", "train-b"}
    assert all("curator" not in report for report in seen[1]["reports"].values())


def test_invalid_curator_does_not_commit_pending_feedback(tmp_path):
    path = tmp_path / "ledger.json"
    proposer = TwoStagePlaybookProposer(
        reflector=lambda record: review(record["instance_id"], "helpful"),
        curator=lambda *_: {"reasoning": "Invalid.", "operations": [{"type": "BAD"}]},
        token_counter=lambda text: len(text.split()), global_counter_path=path,
    )
    with pytest.raises(ValueError):
        proposer({"rules": seed().serialize()}, {"rules": [{"instance_id": "train-a"}]}, ["rules"])
    assert proposer.global_counters.feedback_for(seed())["events"] == {}
    assert not path.exists()


def test_revised_text_does_not_inherit_counters_or_unrelated_history():
    ledger = GlobalPlaybookCounters()
    apply(ledger, seed(), [review("train-a", "harmful")], [{"instance_id": "train-a"}])
    revised = RejectPlaybook((PlaybookBullet("plan-00002", "A revised rejection condition.", lineage=("plan-00001",)),))
    assert ledger.hydrate(revised).bullets[0].harmful == 0
    assert ledger.feedback_for(revised)["events"] == {}
    with pytest.raises(ValueError, match="zero counters"):
        validate_curator_proposal(seed(), RejectPlaybook((PlaybookBullet("plan-00002", "New.", neutral=1),)))


def test_hpc_curator_receives_required_history_and_cache_changes(tmp_path):
    from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents

    calls = []

    class Executor:
        run_dir = tmp_path

        def run_wave(self, role, items):
            calls.append((role, items))
            return [{"agent_output": {"reasoning": "Retain.", "operations": []}}]

    ledger = GlobalPlaybookCounters()
    refs = [review("train-a", "harmful")]
    _, history = apply(ledger, seed(), refs, [{"instance_id": "train-a"}])
    agents = HPCPlaybookProposalAgents(Executor(), maximum_tokens=2048, evidence_contract="ace_v2")
    agents.curate(seed(), refs)
    old_dir = calls[-1][1][0]["evidence_dir"]
    agents.curate(seed(), refs, feedback_history=history)
    item = calls[-1][1][0]
    from pathlib import Path
    evidence = Path(item["evidence_dir"])
    assert str(evidence) != old_dir
    manifest = json.loads((evidence / "manifest.json").read_text())
    assert "rule_feedback_history_index.json" in manifest["required_files"]
    assert "rule_feedback_history.json" in manifest["optional_files"]
    assert "rule_feedback_history.json" in manifest["files"]
    index = json.loads((evidence / "rule_feedback_history_index.json").read_text())
    assert index["bullets"][0]["history_observations"] == {"helpful": 0, "harmful": 1, "neutral": 0}
    assert json.loads((evidence / "rule_feedback_history.json").read_text()) == history
    agents.curate(seed(), refs, feedback_history=history)
    assert calls[-1][1][0]["evidence_dir"] == str(evidence)
