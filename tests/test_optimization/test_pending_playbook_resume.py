from copy import deepcopy
import json
from pathlib import Path
import pickle
import random
import os
import shutil
from types import SimpleNamespace

from gepa.core.state import EvaluationCache
import pytest

from scripts.internal.prepare_pending_playbook_resume import prepare, encoded
from src.optimization.pending_playbook_resume import MARKER, PendingDraw, file_hash, replay_pending_proposal
from src.optimization.playbook import PlaybookBullet, RejectPlaybook
from src.optimization.playbook_adapter import TwoStagePlaybookProposer


def fail(*args, **kwargs):
    raise AssertionError("completed Agent work must not be rerun")


@pytest.fixture
def checkpoint(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    parent = {"rules": RejectPlaybook((PlaybookBullet("plan-00002", "Rule", lineage=("plan-00001",)),)).serialize()}
    cache = EvaluationCache()
    cache.put(parent, "pair-a", {"original": "checker output"}, 1)
    state = {"i": 6, "program_candidates": [parent], "evaluation_cache": cache,
             "total_num_evals": 20, "preserved_frontier": {"x": [0]},
             "full_program_trace": [{"i": i, "complete": True} for i in range(6)] + [
                 {"i": 6, "selected_program_candidate": 0, "subsample_ids": ["pair-a"], "subsample_scores": [1]}]}
    resume = {"gepa_state_i": 6, "random_state": "post-draw", "sampler": {"epoch": 1},
              "reflection_failures": [{"error_type": "ValueError", "error": "lineage failure"}]}
    reviews = [{"instance_id": "pair-a", "bullet_tags": [{"id": "plan-00002", "tag": "helpful"}]}]
    counted = RejectPlaybook((PlaybookBullet("plan-00002", "Rule", helpful=1, lineage=("plan-00001",)),))
    files = {"gepa_state.bin": pickle.dumps(state), "gepa_resume_state.json": encoded(resume),
             "global_counter_ledger.json": b"{}", "candidates.json": encoded([parent]),
             "run_manifest.json": encoded({"semantic_config": {"source": {}, "runtime_config": "unchanged"}}),
             "task.json": encoded({"fingerprint": "exact", "prompt_values": {"counted_internal_playbook": counted.serialize()}}),
             "output.json": encoded({"status": "completed", "fingerprint": "exact", "agent_output": {"reasoning": "no change", "operations": []}}),
             "reviews.json": encoded(reviews)}
    for name, content in files.items():
        (root / name).write_bytes(content)
    authority = {"run_name": root.name, "failed_iteration": 7, "candidate_count": 1,
                 "error": "lineage failure", "recovery_id": "iteration7",
                 "curator_task": "task.json", "curator_result": "output.json", "reviews": "reviews.json",
                 "files": {name: file_hash(root / name) for name in files}, "replacement_source_hashes": {}}
    return root, authority, state, resume


def test_prepare_preserves_six_rounds_rng_cache_and_ledger(checkpoint):
    root, authority, original, old_resume = checkpoint
    before = {n: (root / n).read_bytes() for n in authority["files"]}
    assert prepare(root, authority)["status"] == "dry_run"
    assert before == {n: (root / n).read_bytes() for n in before}
    assert prepare(root, authority, apply=True)["pending_iteration"] == 7
    state = pickle.loads((root / "gepa_state.bin").read_bytes())
    assert state["i"] == 5 and state["total_num_evals"] == 19
    assert state["full_program_trace"] == original["full_program_trace"][:6]
    for key in ("program_candidates", "evaluation_cache", "preserved_frontier"):
        assert state[key] == original[key]
    resume = json.loads((root / "gepa_resume_state.json").read_text())
    assert resume["random_state"] == old_resume["random_state"]
    assert resume["sampler"] == old_resume["sampler"]
    assert resume["reflection_failures"] == []
    for name, content in before.items():
        assert (root / "recovery_backups/iteration7" / name).read_bytes() == content
    for name in ("global_counter_ledger.json", "candidates.json", "task.json", "output.json", "reviews.json"):
        assert (root / name).read_bytes() == before[name]
    # Preparation is idempotent, including after resumed work advances.
    state["i"] = 7
    (root / "gepa_state.bin").write_bytes(pickle.dumps(state))
    assert prepare(root, authority, apply=True)["status"] == "already_prepared"
    assert pickle.loads((root / "gepa_state.bin").read_bytes())["i"] == 7


def test_tampered_authority_fails_without_writes(checkpoint):
    root, authority, _, _ = checkpoint
    (root / "candidates.json").write_text("changed")
    with pytest.raises(ValueError, match="authority mismatch"):
        prepare(root, authority, apply=True)
    assert not (root / MARKER).exists()
    assert not (root / "recovery_backups").exists()


def test_pending_draw_does_not_advance_rng_and_round_eight_delegates(checkpoint):
    root, authority, original, _ = checkpoint
    prepare(root, authority, apply=True)
    pending = json.loads((root / MARKER).read_text())
    rng = random.Random(42)
    selector = SimpleNamespace(select_candidate_idx=lambda state: rng.randrange(100))
    sampler = SimpleNamespace(next_minibatch_ids=lambda loader, state: [rng.randrange(100)])
    draw = PendingDraw(selector, sampler, pending)
    state = SimpleNamespace(i=6, program_candidates=original["program_candidates"])
    loader = SimpleNamespace(all_ids=lambda: ["pair-a"])
    saved = rng.getstate()
    assert draw.select_candidate_idx(state) == 0
    assert draw.next_minibatch_ids(loader, state) == ["pair-a"]
    assert saved == rng.getstate()
    state.i = 7
    draw.select_candidate_idx(state)
    draw.next_minibatch_ids(loader, state)
    assert saved != rng.getstate()


def test_precheckpoint_pending_draw_consumes_and_verifies_original_draw(checkpoint):
    _, _, original, _ = checkpoint
    pending = {
        "draw_policy": "replay_underlying_and_verify",
        "trace": {"i": 0, "selected_program_candidate": 0, "subsample_ids": ["pair-a"]},
        "parent": original["program_candidates"][0],
    }
    calls = []
    selector = SimpleNamespace(
        select_candidate_idx=lambda state: calls.append("selector") or 0
    )
    sampler = SimpleNamespace(
        next_minibatch_ids=lambda loader, state: calls.append("sampler") or ["pair-a"]
    )
    draw = PendingDraw(selector, sampler, pending)
    state = SimpleNamespace(i=0, program_candidates=original["program_candidates"])
    loader = SimpleNamespace(all_ids=lambda: ["pair-a"])
    assert draw.select_candidate_idx(state) == 0
    assert draw.next_minibatch_ids(loader, state) == ["pair-a"]
    assert calls == ["selector", "sampler"]


def test_prepare_precheckpoint_candidate_reruns_whole_screen(tmp_path):
    root = tmp_path / "precheckpoint"
    root.mkdir()
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    runtime = repo / "configs/runtime.yaml"
    runtime.write_text("schema_version: 1\n")
    parent = {
        "rules": RejectPlaybook(
            (PlaybookBullet("plan-00001", "The Plan is a placeholder."),)
        ).serialize()
    }
    state = {
        "i": -1,
        "program_candidates": [parent],
        "evaluation_cache": EvaluationCache(),
        "total_num_evals": 36,
        "full_program_trace": [],
    }
    resume = {
        "gepa_state_i": -1,
        "random_state": "pre-draw",
        "sampler": {"epoch": -1},
        "reflection_failures": [],
    }
    reviews = [{"instance_id": "pair-a", "key_insight": "none"}]
    curator_output = {"reasoning": "keep seed", "operations": []}
    files = {
        "gepa_state.bin": pickle.dumps(state),
        "gepa_resume_state.json": encoded(resume),
        "global_counter_ledger.json": b"{}",
        "candidates.json": encoded([parent]),
        "run_manifest.json": encoded(
            {"semantic_config": {"source": {}, "runtime_config": "unchanged"}}
        ),
        "task.json": encoded(
            {
                "fingerprint": "exact",
                "prompt_values": {"counted_internal_playbook": parent["rules"]},
            }
        ),
        "output.json": encoded(
            {
                "status": "completed",
                "fingerprint": "exact",
                "agent_output": curator_output,
            }
        ),
        "reviews.json": encoded(reviews),
        "pair.json": encoded(
            {
                "instance_id": "pair-a",
                "pair_output": {"resolved": {}, "unresolved": {}},
                "score": 0,
            }
        ),
    }
    for name, content in files.items():
        (root / name).write_bytes(content)
    authority = {
        "schema_version": 2,
        "recovery_kind": "candidate_evaluation_before_checkpoint_v1",
        "run_name": root.name,
        "recovery_id": "candidate-screen",
        "saved_state_i": -1,
        "saved_metric_calls": 36,
        "candidate_count": 1,
        "selected_program_candidate": 0,
        "subsample_ids": ["pair-a"],
        "validation_pair_count": 36,
        "proposed_rule_count": 1,
        "curator_task": "task.json",
        "curator_result": "output.json",
        "reviews": "reviews.json",
        "parent_evidence": ["pair.json"],
        "files": {name: file_hash(root / name) for name in files},
        "replacement_runtime_config": {
            "path": "configs/runtime.yaml",
            "previous_sha256": "unchanged",
            "replacement_sha256": file_hash(runtime),
        },
        "replacement_source_hashes": {},
    }
    before_state = (root / "gepa_state.bin").read_bytes()
    summary = prepare(root, authority, apply=True, repo=repo)
    assert summary["candidate_checker_tasks_to_rerun"] == 2
    assert summary["validation_pairs_after_screen"] == 36
    assert (root / "gepa_state.bin").read_bytes() == before_state
    assert json.loads((root / "gepa_resume_state.json").read_text()) == resume
    marker = json.loads((root / MARKER).read_text())
    assert marker["draw_policy"] == "replay_underlying_and_verify"
    assert marker["trace"]["subsample_ids"] == ["pair-a"]
    assert marker["parent_outputs"] == [{"resolved": {}, "unresolved": {}}]
    manifest = json.loads((root / "run_manifest.json").read_text())
    assert manifest["semantic_config"]["runtime_config"] == file_hash(runtime)


def test_precheckpoint_recovery_can_be_explicitly_replaced(tmp_path):
    root = tmp_path / "precheckpoint"
    root.mkdir()
    repo = tmp_path / "repo"
    (repo / "configs").mkdir(parents=True)
    runtime_v1 = repo / "configs/runtime-v1.yaml"
    runtime_v1.write_text("version: 1\n")
    runtime_v2 = repo / "configs/runtime-v2.yaml"
    runtime_v2.write_text("version: 2\n")
    prompt_v1 = repo / "configs/prompt-v1.yaml"
    prompt_v1.write_text("prompt: 1\n")
    prompt_v2 = repo / "configs/prompt-v2.yaml"
    prompt_v2.write_text("prompt: 2\n")
    parent = {
        "rules": RejectPlaybook(
            (PlaybookBullet("plan-00001", "The Plan is a placeholder."),)
        ).serialize()
    }
    state = {
        "i": -1,
        "program_candidates": [parent],
        "evaluation_cache": EvaluationCache(),
        "total_num_evals": 1,
        "full_program_trace": [],
    }
    files = {
        "gepa_state.bin": pickle.dumps(state),
        "gepa_resume_state.json": encoded(
            {"gepa_state_i": -1, "reflection_failures": []}
        ),
        "run_manifest.json": encoded(
            {
                "semantic_config": {
                    "source": {},
                    "runtime_config": "original",
                    "prompt_bundle": file_hash(prompt_v1),
                }
            }
        ),
        "task.json": encoded(
            {
                "fingerprint": "exact",
                "prompt_values": {"counted_internal_playbook": parent["rules"]},
            }
        ),
        "output.json": encoded(
            {
                "status": "completed",
                "fingerprint": "exact",
                "agent_output": {"reasoning": "keep", "operations": []},
            }
        ),
        "reviews.json": encoded([{"instance_id": "pair-a"}]),
        "pair.json": encoded(
            {"instance_id": "pair-a", "pair_output": {}, "score": 0}
        ),
    }
    for name, content in files.items():
        (root / name).write_bytes(content)

    def authority(recovery_id, runtime, previous):
        return {
            "schema_version": 2,
            "recovery_kind": "candidate_evaluation_before_checkpoint_v1",
            "run_name": root.name,
            "recovery_id": recovery_id,
            "saved_state_i": -1,
            "saved_metric_calls": 1,
            "candidate_count": 1,
            "selected_program_candidate": 0,
            "subsample_ids": ["pair-a"],
            "validation_pair_count": 1,
            "proposed_rule_count": 1,
            "curator_task": "task.json",
            "curator_result": "output.json",
            "reviews": "reviews.json",
            "parent_evidence": ["pair.json"],
            "files": {name: file_hash(root / name) for name in files},
            "replacement_runtime_config": {
                "path": str(runtime.relative_to(repo)),
                "previous_sha256": previous,
                "replacement_sha256": file_hash(runtime),
            },
            "replacement_source_hashes": {},
        }

    first = authority("candidate-screen-v1", runtime_v1, "original")
    prepare(root, first, apply=True, repo=repo)
    second = authority(
        "candidate-screen-v2", runtime_v2, file_hash(runtime_v1)
    )
    second["replaces_recovery_id"] = "candidate-screen-v1"
    second["replacement_prompt_bundle"] = {
        "path": str(prompt_v2.relative_to(repo)),
        "previous_sha256": file_hash(prompt_v1),
        "replacement_sha256": file_hash(prompt_v2),
    }
    second["files"] = {name: file_hash(root / name) for name in files}
    prepare(root, second, apply=True, repo=repo)

    marker = json.loads((root / MARKER).read_text())
    assert marker["authority"]["recovery_id"] == "candidate-screen-v2"
    assert (root / "recovery_backups/candidate-screen-v1").is_dir()
    assert (root / "recovery_backups/candidate-screen-v2").is_dir()
    manifest = json.loads((root / "run_manifest.json").read_text())
    assert manifest["semantic_config"]["runtime_config"] == file_hash(runtime_v2)
    assert manifest["semantic_config"]["prompt_bundle"] == file_hash(prompt_v2)


def test_replay_uses_frozen_agents_and_deduplicates_counter_after_yield(checkpoint):
    root, authority, original, _ = checkpoint
    prepare(root, authority, apply=True)
    ledger = root / "test_ledger.json"
    state = SimpleNamespace(i=6, program_candidates=original["program_candidates"])
    search = SimpleNamespace(selector=SimpleNamespace(select_candidate_idx=fail), sampler=SimpleNamespace(next_minibatch_ids=fail))
    cases = [SimpleNamespace(instance_id="pair-a")]
    def make_proposer():
        return TwoStagePlaybookProposer(reflector=fail, curator=fail, batch_reflector=fail,
            token_counter=len, maximum_tokens=1000, global_counter_path=ledger,
            review_validator=lambda raw, **kw: raw)
    outputs = []
    for _ in range(2):  # Simulate a controller yielding after proposal completion.
        proposer = make_proposer()
        adapter = SimpleNamespace(evaluate=fail, propose_new_texts=proposer,
            _trace=lambda case, **kw: {"instance_id": case.instance_id})
        with replay_pending_proposal(root, search, adapter) as (selector, sampler):
            selector.select_candidate_idx(state)
            sampler.next_minibatch_ids(SimpleNamespace(all_ids=lambda: ["pair-a"]), state)
            batch = adapter.evaluate(cases, original["program_candidates"][0], capture_traces=True)
            assert batch.scores == [1]
            outputs.append(proposer(original["program_candidates"][0], {"rules": batch.trajectories}, ["rules"]))
            with pytest.raises(AssertionError):  # New candidate evaluation is ordinary work.
                adapter.evaluate(cases, outputs[-1], capture_traces=False)
        assert adapter.evaluate is fail and proposer.curator is fail
    assert outputs[0] == outputs[1]
    assert RejectPlaybook.parse(outputs[0]["rules"]).bullets[0].helpful == 1


def test_no_marker_leaves_existing_workflow_untouched(tmp_path):
    selector, sampler = object(), object()
    search = SimpleNamespace(selector=selector, sampler=sampler)
    with replay_pending_proposal(tmp_path, search, object()) as pair:
        assert pair == (selector, sampler)


@pytest.fixture
def completed_screen(tmp_path):
    from src.optimization.playbook import apply_curator_operations

    root = tmp_path / "completed-screen"
    root.mkdir()
    parent = {"rules": RejectPlaybook((PlaybookBullet("plan-00001", "The Plan is a placeholder."),)).serialize()}
    curator_output = {"reasoning": "Add a supported condition", "operations": [
        {"type": "ADD", "content": "The Plan contradicts the required result.", "supporting_instance_ids": ["pair-a"]}
    ]}
    candidate = {"rules": apply_curator_operations(RejectPlaybook.parse(parent["rules"]), curator_output).serialize()}
    cache = EvaluationCache()
    cache.put(parent, "pair-a", {"original": "parent"}, 1)
    cache.put(candidate, "pair-a", {"original": "old candidate"}, 0)
    state = {"i": 0, "program_candidates": [parent], "evaluation_cache": cache,
             "total_num_evals": 3, "preserved_frontier": {"validation": [0]},
             "full_program_trace": [{"i": 0, "selected_program_candidate": 0,
                                     "subsample_ids": ["pair-a"], "subsample_scores": [1],
                                     "new_subsample_scores": [0]}]}
    resume = {"gepa_state_i": 0, "successful_proposals": 1, "accepted_candidates": 0,
              "random_state": "post-draw", "sampler": {"epoch": 0}, "reflection_failures": []}
    files = {
        "gepa_state.bin": pickle.dumps(state), "gepa_resume_state.json": encoded(resume),
        "global_counter_ledger.json": b"{}", "result.json": b'{"completed":true}',
        "run_manifest.json": encoded({"semantic_config": {"source": {}}}),
        "task.json": encoded({"fingerprint": "exact", "prompt_values": {"counted_internal_playbook": parent["rules"]}}),
        "output.json": encoded({"status": "completed", "fingerprint": "exact", "agent_output": curator_output}),
        "reviews.json": encoded([{"instance_id": "pair-a"}]),
        "pair.json": encoded({"instance_id": "pair-a", "pair_output": {"original": "parent"}, "score": 1}),
    }
    for name, data in files.items():
        (root / name).write_bytes(data)
    authority = {
        "recovery_kind": "completed_rejected_candidate_screen_v1", "run_name": root.name,
        "recovery_id": "checker-replay", "saved_state_i": 0, "saved_metric_calls": 3,
        "candidate_count": 1, "selected_program_candidate": 0, "validation_pair_count": 1,
        "proposed_rule_count": 2, "subsample_ids": ["pair-a"],
        "curator_task": "task.json", "curator_result": "output.json", "reviews": "reviews.json",
        "parent_evidence": ["pair.json"], "replacement_source_hashes": {},
        "files": {name: file_hash(root / name) for name in files},
    }
    return root, authority, state, resume, candidate


def test_completed_rejected_screen_reopens_only_candidate_checks(completed_screen):
    root, authority, original, old_resume, candidate = completed_screen
    before = {name: (root / name).read_bytes() for name in authority["files"]}
    assert prepare(root, authority)["candidate_checker_tasks_to_rerun"] == 2
    assert before == {name: (root / name).read_bytes() for name in before}
    summary = prepare(root, authority, apply=True)
    assert summary["completed_iterations"] == 0 and summary["pending_iteration"] == 1
    state = pickle.loads((root / "gepa_state.bin").read_bytes())
    assert state["i"] == -1 and state["total_num_evals"] == 1
    assert state["full_program_trace"] == []
    assert state["program_candidates"] == original["program_candidates"]
    assert state["preserved_frontier"] == original["preserved_frontier"]
    assert state["evaluation_cache"].get(candidate, "pair-a") is None
    assert state["evaluation_cache"].get(original["program_candidates"][0], "pair-a").score == 1
    resume = json.loads((root / "gepa_resume_state.json").read_text())
    assert resume["successful_proposals"] == 0 and resume["gepa_state_i"] == -1
    assert resume["random_state"] == old_resume["random_state"]
    assert resume["sampler"] == old_resume["sampler"]
    marker = json.loads((root / MARKER).read_text())
    assert marker["draw_policy"] == "frozen_completed_draw" and marker["trace"]["i"] == 0
    assert not (root / "result.json").exists()
    for name, data in before.items():
        assert (root / "recovery_backups/checker-replay" / name).read_bytes() == data
    for name in ("global_counter_ledger.json", "task.json", "output.json", "reviews.json", "pair.json"):
        assert (root / name).read_bytes() == before[name]
    assert prepare(root, authority, apply=True)["status"] == "already_prepared"


def test_completed_screen_rejects_accepted_or_incompatible_cache_without_writes(completed_screen):
    root, authority, state, _, _ = completed_screen
    state["full_program_trace"][0]["new_subsample_scores"] = [2]
    (root / "gepa_state.bin").write_bytes(pickle.dumps(state))
    authority["files"]["gepa_state.bin"] = file_hash(root / "gepa_state.bin")
    before = {name: (root / name).read_bytes() for name in authority["files"]}
    with pytest.raises(ValueError, match="not the pinned completed rejected"):
        prepare(root, authority, apply=True)
    assert before == {name: (root / name).read_bytes() for name in before}
    assert not (root / "recovery_backups").exists()


def test_unlaunched_preparation_can_be_reapplied_from_exact_backup(completed_screen):
    root, authority, original, _, candidate = completed_screen
    original_bytes = {name: (root / name).read_bytes() for name in authority["files"]}
    prepare(root, authority, apply=True)
    prepared_state = (root / "gepa_state.bin").read_bytes()
    old_backup = root / "recovery_backups" / authority["recovery_id"]
    # Preserve the unlaunched preparation, then restore only byte-identical
    # checkpoint metadata/result from its verified original backup.
    unlaunched_backup = root / "recovery_backups" / "unlaunched-preparation"
    unlaunched_backup.mkdir()
    for name in ("gepa_state.bin", "gepa_resume_state.json", "run_manifest.json", MARKER):
        (unlaunched_backup / name).write_bytes((root / name).read_bytes())
    for name in ("gepa_state.bin", "gepa_resume_state.json", "run_manifest.json", "result.json"):
        assert file_hash(old_backup / name) == authority["files"][name]
        (root / name).write_bytes((old_backup / name).read_bytes())
    new_authority = deepcopy(authority)
    new_authority["recovery_id"] = "checker-replay-new-config"
    assert prepare(root, new_authority, apply=True)["candidate_checker_tasks_to_rerun"] == 2
    state = pickle.loads((root / "gepa_state.bin").read_bytes())
    assert state["i"] == -1 and state["total_num_evals"] == 1
    assert state["program_candidates"] == original["program_candidates"]
    assert state["evaluation_cache"].get(candidate, "pair-a") is None
    assert (unlaunched_backup / "gepa_state.bin").read_bytes() == prepared_state
    for name in ("global_counter_ledger.json", "task.json", "output.json", "reviews.json", "pair.json"):
        assert (root / name).read_bytes() == original_bytes[name]
    for name, data in original_bytes.items():
        assert (old_backup / name).read_bytes() == data


def test_resume_supervisor_targets_original_scientific_run():
    import yaml
    path = Path("configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_resume7_supervisor_20260922.yaml")
    launch = yaml.safe_load(path.read_text())
    args = launch["arguments"]
    option = lambda key: args[args.index(key) + 1]
    assert option("--gepa-config") == "configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_20260921.yaml"
    assert option("--job-name").endswith("formal24-8it-v1-20260921")
    assert option("--target-iterations") == "8"
    assert option("--cpus") == "1" and option("--mem") == "4G"
    assert option("--ulhpc-config") == "configs/ulhpc_submit.yaml"
    runtime = yaml.safe_load(Path(option("--gepa-config")).read_text())
    assert "checkpoint_import" not in runtime
    assert runtime["paths"]["run_dir"].endswith("formal24-8it-v1-20260921")


def test_codex_resume_supervisor_targets_original_formal15_run():
    import yaml

    path = Path(
        "configs/gepa_verified_paired_ace_codex_formal24_15it_v1_resume1_supervisor_20260925.yaml"
    )
    launch = yaml.safe_load(path.read_text())
    args = launch["arguments"]
    option = lambda key: args[args.index(key) + 1]
    runtime_path = "configs/gepa_verified_paired_ace_codex_formal24_15it_v1_20260925.yaml"
    assert option("--gepa-config") == runtime_path
    assert option("--job-name") == "verified-paired-ace-codex-formal24-15it-v1-20260925"
    assert option("--target-iterations") == "15"
    assert option("--cpus") == "1" and option("--mem") == "4G"
    runtime = yaml.safe_load(Path(runtime_path).read_text())
    assert runtime["inputs"]["repo_checker_contract"].endswith(
        "offline_gepa_paired_binary_contract_v2_20260925.yaml"
    )
    assert runtime["repo_checker"]["output_contract"] == "binary_v2"
    assert runtime["paths"]["run_dir"].endswith(
        "ace-codex-formal24-15it-v1-20260925"
    )


@pytest.mark.skipif(not os.environ.get("VIBE_V1_RECOVERY_FIXTURE"), reason="optional frozen v1 evidence")
def test_real_v1_recovery_yield_then_finishes_eight_without_retraining(tmp_path):
    """Real v1 state/evidence + real GEPA; only *new* Agent work is simulated."""
    import yaml
    from gepa.core.adapter import EvaluationBatch
    from src.exceptions import ControllerYield
    from src.optimization.playbook_runner import run_playbook_search
    from src.optimization.playbook_adapter import PairedRepoPlaybookGEPAAdapter
    from src.optimization.playbook_cli import _token_counter
    from src.optimization.paired_playbook import validate_paired_reflector_review
    from src.optimization.repo_playbook import render_concern_playbook

    source = Path(os.environ["VIBE_V1_RECOVERY_FIXTURE"])
    root = tmp_path / source.name
    shutil.copytree(source, root)
    original = pickle.loads((root / "gepa_state.bin").read_bytes())
    authority = json.loads(Path("configs/recovery/paired_levels_v1_iteration7_20260922.json").read_text())
    prepare(root, authority, apply=True)
    runtime = Path("configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_20260921.yaml")
    config = yaml.safe_load(runtime.read_text())
    count_tokens = _token_counter(config["models"]["checker"]["model"])
    calls = {"reflection": [], "curator": 0, "new_evaluation": 0}
    first_slice = True

    def build_adapter():
        def reflect(records):
            calls["reflection"].append([r["instance_id"] for r in records])
            return [{"instance_id": r["instance_id"], "pair_analysis": None,
                     "uncertainty": None, "reusable_concerns": [],
                     "bullet_tags": [{"id": b.id, "tag": "neutral", "attribution": None,
                                      "confidence": "low"}
                                     for b in RejectPlaybook.parse(r["internal_playbook"]).bullets]}
                    for r in records]
        def curate(*args):
            calls["curator"] += 1
            return {"reasoning": "simulation only", "operations": []}
        proposer = TwoStagePlaybookProposer(reflector=fail, batch_reflector=reflect, curator=curate,
            token_counter=count_tokens, maximum_tokens=2048, harmful_weight=2,
            global_counter_path=root / "global_counter_ledger.json",
            review_validator=validate_paired_reflector_review, visible_renderer=render_concern_playbook,
            semantic_refiner=fail)
        adapter = PairedRepoPlaybookGEPAAdapter(fail, proposer, levels=True,
                                               token_counter=count_tokens, maximum_bullet_tokens=64)
        def evaluate(batch, candidate, capture_traces=False):
            nonlocal first_slice
            # GEPA's resume preflight asks for one seed example before loading
            # state; the real HPC adapter reuses the original completion.
            if not capture_traces and candidate == original["program_candidates"][0] and all(
                c.instance_id in original["prog_candidate_val_subscores"][0] for c in batch
            ):
                return EvaluationBatch(outputs=[{} for _ in batch], scores=[
                    original["prog_candidate_val_subscores"][0][c.instance_id] for c in batch])
            calls["new_evaluation"] += 1
            if first_slice:
                first_slice = False
                raise ControllerYield(reason="test_new_candidate_wait", batch_dir="test", job_id="test")
            playbook = RejectPlaybook.parse(candidate["rules"])
            outputs = [{"instance_id": c.instance_id, "simulation": True} for c in batch]
            traces = [adapter._trace(c, playbook=playbook, visible=render_concern_playbook(playbook),
                                     output=o, score=0) for c, o in zip(batch, outputs)]
            return EvaluationBatch(outputs=outputs, scores=[0] * len(batch),
                                   trajectories=traces if capture_traces else None)
        adapter.evaluate = evaluate
        return adapter

    def run():
        return run_playbook_search(dataset_snapshot=Path(config["paths"]["dataset_snapshot"]),
            initial_playbook_path=Path(config["paths"]["initial_rules"]), run_dir=root,
            adapter=build_adapter(), max_metric_calls=1200, max_iterations=8, seed=42,
            skip_perfect_score=True, perfect_score=1.0,
            train_instance_ids=config["inputs"]["train_instance_ids"],
            reflection_minibatch_size=24, runtime_config_path=runtime,
            prompt_bundle_path=Path(config["inputs"]["prompt_bundle"]),
            abort_on_operational_incomplete=True, data_unit="within_task_plan_pair")

    assert run() is None  # Yield only when new candidate evaluation is requested.
    assert calls["reflection"] == [] and calls["curator"] == 0
    ledger_after_first = (root / "global_counter_ledger.json").read_bytes()
    result = run()
    assert result is not None
    final = pickle.loads((root / "gepa_state.bin").read_bytes())
    assert final["i"] == 7
    assert final["program_candidates"] == original["program_candidates"]  # simulated candidates tie/lose
    assert final["full_program_trace"][:6] == original["full_program_trace"][:6]
    seventh = final["full_program_trace"][6]
    for key, value in original["full_program_trace"][6].items():
        assert seventh[key] == value
    assert len(calls["reflection"]) == 1 and calls["curator"] == 1  # only round 8
    # Round 8's neutral reviews may extend observation bookkeeping, not counts.
    final_counts = json.loads((root / "global_counter_ledger.json").read_text())["counts"]
    first_counts = json.loads(ledger_after_first)["counts"]
    assert all(final_counts[k] == v for k, v in first_counts.items())
    assert all(v == {"helpful": 0, "harmful": 0}
               for k, v in final_counts.items() if k not in first_counts)
    resume = json.loads((root / "gepa_resume_state.json").read_text())
    assert resume["gepa_state_i"] == 7 and resume["reflection_failures"] == []
