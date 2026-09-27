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
