#!/usr/bin/env python3
"""Deterministic reliability checks for within-task PCE contrasts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any


_TERMINAL_OUTCOMES = frozenset({"resolved", "unresolved"})


def audit_patch_outcome_consistency(
    observations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reject a contrast when identical code received conflicting outcomes.

    A single evaluator execution remains an immutable raw authority. This
    check operates only when multiple executions of the same task are compared:
    if the exact same patch bytes were both resolved and unresolved, the task
    contains evaluator/environment nondeterminism and cannot identify a Plan
    effect.
    """

    outcomes_by_patch: dict[str, set[str]] = defaultdict(set)
    terminal_observations = 0
    for observation in observations:
        outcome = str(observation.get("outcome", ""))
        if outcome not in _TERMINAL_OUTCOMES:
            continue
        patch_sha256 = str(observation.get("patch_sha256", ""))
        if not patch_sha256:
            raise ValueError("terminal PCE observation requires patch_sha256")
        terminal_observations += 1
        outcomes_by_patch[patch_sha256].add(outcome)

    conflicts = sorted(
        patch_sha256
        for patch_sha256, outcomes in outcomes_by_patch.items()
        if outcomes == _TERMINAL_OUTCOMES
    )
    return {
        "schema_version": 1,
        "terminal_observations": terminal_observations,
        "evaluator_nondeterministic": bool(conflicts),
        "conflicting_patch_sha256": conflicts,
        "eligible_for_plan_contrast": not conflicts,
        "reason_codes": ["identical_patch_conflicting_outcomes"] if conflicts else [],
    }
