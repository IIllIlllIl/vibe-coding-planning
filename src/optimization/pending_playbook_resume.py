"""Explicit recovery of a paired proposal that failed after Agent completion.

Two checkpoint positions are supported. A post-draw checkpoint replays the
frozen draw without consuming randomness. A pre-draw checkpoint delegates the
draw to the original selector/sampler and verifies the frozen result. Nothing
in this module changes an Agent response or a candidate score.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from gepa.core.adapter import EvaluationBatch

from src.optimization.playbook import RejectPlaybook
from src.optimization.repo_playbook import render_concern_playbook

MARKER = "pending_proposal_resume.json"


def load_training_replay(config):
    """An explicit one-proposal pilot, separate from legacy failed-run recovery."""
    manifest = json.loads((config.dataset_snapshot / "manifest.json").read_text())
    reference = manifest.get("training_replay")
    if reference is None:
        return None
    path = config.dataset_snapshot / reference["path"]
    if (path.resolve().parent != config.dataset_snapshot.resolve()
            or file_hash(path) != reference["sha256"]):
        raise ValueError("historical training replay authority mismatch")
    replay = json.loads(path.read_text())
    train = {json.loads(line)["instance_id"] for line in (config.dataset_snapshot / "train.jsonl").open() if line.strip()}
    validation = {json.loads(line)["instance_id"] for line in (config.dataset_snapshot / "validation.jsonl").open() if line.strip()}
    ids = replay["trace"]["subsample_ids"]
    if (replay.get("schema_version") != 1 or config.search.max_iterations != 1
            or len(ids) != config.search.reflection_minibatch_size or len(set(ids)) != len(ids)
            or not set(ids) <= train or replay["parent"]["rules"] != config.initial_rules_path.read_text().strip()
            or [row["instance_id"] for row in replay["records"]] != ids
            or [row["instance_id"] for row in replay["parent_rows"]] != ids
            or any(row.get("split") != "train" for row in replay["records"])
            or any(record["pce_runs"] != row["output"]["pce_runs"] for record, row in
                   zip(replay["records"], replay["parent_rows"], strict=True))
            or {row["instance_id"] for row in replay["validation"]} != validation
            or any(row.get("split") != "validation" for row in replay["validation"])
            or any(event["instance_id"] not in train or event["instance_id"] in set(ids)
                   for event in replay["ledger"]["history"].values())
            or any(report["instance_id"] not in train for report in replay["ledger"]["reports"].values())):
        raise ValueError("historical replay must bind one frozen training minibatch, prior training history and seed validation")
    return replay


@contextmanager
def replay_safe_pce_training(config, search, adapter):
    """Reuse original parent observations, not old Ref/Cur or candidate outputs.

    New candidate evaluation still calls the unchanged Safe PCE backend. Seed
    validation observations never enter training trajectories or role inputs.
    """
    replay = load_training_replay(config)
    if replay is None:
        yield search.selector, search.sampler
        return
    from src.optimization.safe_pce_learning import _append, _validate_learning_evidence, outcome_score
    from src.optimization.audit import text_sha256
    digest = text_sha256(replay["parent"]["rules"])
    rows = [*replay["parent_rows"], *replay["validation"]]
    for row in rows:
        if row["candidate_sha256"] != digest:
            raise ValueError("historical replay candidate mismatch")
        runs = row["output"]["pce_runs"]
        for run in runs:
            _validate_learning_evidence(run["pce_output"])
            if run["score"] != outcome_score(run["pce_output"], mode=adapter.mode, instance_id=row["instance_id"]):
                raise ValueError("historical replay outcome mismatch")
        if row["score"] != sum(run["score"] for run in runs) / len(runs):
            raise ValueError("historical replay aggregate score mismatch")
    _append(config.run_dir / "safe_pce_evaluations.jsonl", rows)
    original = adapter.evaluate
    draw = PendingDraw(search.selector, search.sampler, replay)
    seed_val = {row["instance_id"]: row for row in replay["validation"]}

    def evaluate(batch, candidate, capture_traces=False):
        ids = [case.instance_id for case in batch]
        if candidate == replay["parent"]:
            if not capture_traces and set(ids) == set(seed_val):
                selected = [seed_val[identifier] for identifier in ids]
                return EvaluationBatch(outputs=[r["output"] for r in selected],
                                       scores=[r["score"] for r in selected], trajectories=None)
            if draw.active and capture_traces:
                if ids != replay["trace"]["subsample_ids"]:
                    raise ValueError("historical training draw changed")
                return EvaluationBatch(outputs=[r["output"] for r in replay["parent_rows"]],
                    scores=replay["trace"]["subsample_scores"], trajectories=replay["records"])
        return original(batch, candidate, capture_traces=capture_traces)

    adapter.evaluate = evaluate
    try:
        yield draw, draw
    finally:
        adapter.evaluate = original


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PendingDraw:
    def __init__(self, selector, sampler, pending):
        self.selector = selector
        self.sampler = sampler
        self.pending = pending
        self.active = False

    @property
    def replay_underlying_draw(self):
        return self.pending.get("draw_policy") == "replay_underlying_and_verify"

    def select_candidate_idx(self, state):
        self.active = state.i == self.pending["trace"]["i"]
        if not self.active:
            return self.selector.select_candidate_idx(state)
        index = self.pending["trace"]["selected_program_candidate"]
        if self.replay_underlying_draw:
            actual = self.selector.select_candidate_idx(state)
            if actual != index:
                raise ValueError("pending proposal candidate draw differs from frozen draw")
        if state.program_candidates[index] != self.pending["parent"]:
            raise ValueError("pending proposal parent differs from frozen parent")
        return index

    def next_minibatch_ids(self, loader, state):
        if state.i != self.pending["trace"]["i"]:
            return self.sampler.next_minibatch_ids(loader, state)
        ids = self.pending["trace"]["subsample_ids"]
        if not self.active or set(ids) - set(loader.all_ids()):
            raise ValueError("pending proposal minibatch is unavailable")
        if self.replay_underlying_draw:
            actual = self.sampler.next_minibatch_ids(loader, state)
            if actual != ids:
                raise ValueError("pending proposal minibatch draw differs from frozen draw")
        return list(ids)


@contextmanager
def replay_pending_proposal(run_dir, search, adapter):
    """Temporarily reuse completed evidence only for the pinned failed round.

    Re-entering after ControllerYield is intentional. Global counter updates
    continue through the normal idempotent ledger, not a copied v2 ledger.
    """
    path = Path(run_dir) / MARKER
    if not path.exists():
        yield search.selector, search.sampler
        return
    pending = json.loads(path.read_text())
    if pending.get("schema_version") != 1:
        raise ValueError("unsupported pending proposal recovery")
    proposer = adapter.propose_new_texts
    evaluate, reflect, curate = adapter.evaluate, proposer.batch_reflector, proposer.curator
    curate_with_history = proposer.curator_with_history
    draw = PendingDraw(search.selector, search.sampler, pending)

    def cached_parent(batch, candidate, capture_traces=False):
        if not (draw.active and capture_traces):
            return evaluate(batch, candidate, capture_traces=capture_traces)
        if candidate != pending["parent"] or [c.instance_id for c in batch] != pending["trace"]["subsample_ids"]:
            raise ValueError("pending parent evaluation input mismatch")
        playbook = RejectPlaybook.parse(candidate["rules"])
        outputs = pending["parent_outputs"]
        scores = pending["trace"]["subsample_scores"]
        traces = [adapter._trace(case, playbook=playbook,
                                 visible=render_concern_playbook(playbook),
                                 output=output, score=score)
                  for case, output, score in zip(batch, outputs, scores, strict=True)]
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=traces)

    def frozen_reviews(records):
        if not draw.active:
            return reflect(records)
        if [r["instance_id"] for r in records] != pending["trace"]["subsample_ids"]:
            raise ValueError("pending Reflection case order mismatch")
        return pending["reviews"]

    def frozen_operations(counted, reviews, records):
        if not draw.active:
            return curate(counted, reviews, records)
        if counted != RejectPlaybook.parse(pending["counted_playbook"]) or reviews != pending["reviews"]:
            raise ValueError("pending Curator input differs from frozen input")
        return pending["curator_output"]

    def frozen_operations_with_history(counted, reviews, records, history):
        if draw.active:
            return frozen_operations(counted, reviews, records)
        return curate_with_history(counted, reviews, records, history)

    adapter.evaluate, proposer.batch_reflector, proposer.curator = cached_parent, frozen_reviews, frozen_operations
    if curate_with_history is not None:
        proposer.curator_with_history = frozen_operations_with_history
    try:
        yield draw, draw
    finally:
        adapter.evaluate, proposer.batch_reflector, proposer.curator = evaluate, reflect, curate
        proposer.curator_with_history = curate_with_history
