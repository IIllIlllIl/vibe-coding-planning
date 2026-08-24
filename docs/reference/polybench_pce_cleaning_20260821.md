# PolyBench PCE repaired-outcome cleaning record

> Frozen decision record for the native-HOME evaluator repair completed on
> 2026-08-21. This file records selection reasons, not model-performance-based
> exclusions.

> Score-use notice (2026-08-24): the evidence-blind 111-case membership remains
> valid provenance, but the associated PCE labels, plans, and Code patches are
> diagnostic only. A later audit found that Agent phases did not verify a clean
> `base_commit`, and final `git add -A` could capture pre-existing SIF worktree
> changes. The membership may be reused prospectively only with fresh
> Plan/Code/Evaluate evidence under a corrected repository and patch boundary.

## Source universe

- Frozen source: `20260814_python113_v11_8c7d9485d1d0`
- Raw PCE run: `python113-v11-pce-20260814`
- Evaluate-only repair: `isolated-home-formal-repair-20260820`
- Repair fingerprint:
  `17dd08f5126bf2a6062b805394099ac79a571de465330791a5579066def2ea7f`
- Exact-v1.1 image-available source cases: 113
- Cases with preserved Plan and Code checkpoints evaluated by the repair: 112
- Cases whose test command completed and produced parsed official evidence: 111

Selection is independent of whether a case resolved and independent of any
guideline or Checker prediction. A case is eligible only when the historical
PCE phases reached Evaluate and the repaired evaluator produced a parsed test
outcome.

## Source exclusions

| Instance | Reason code | Evidence | Cleaning decision |
|---|---|---|---|
| `huggingface__transformers-8747` | `PCE_INCOMPLETE` | The source PCE Code phase exhausted its workflow attempts, so no completed Code checkpoint exists for evaluator-only repair. | Exclude: no fixed patch exists to evaluate. |
| `huggingface__transformers-12981` | `TEST_EXECUTION_TIMEOUT` | Code and test patches applied on their first `git apply`; the evaluator started `python -m pytest /testbed/tests/test_trainer.py -v --junitxml=test-results.xml`, but the command reached the evaluator's 1800-second limit and produced no parsed official result. | Exclude: resolved status is unknown; do not manufacture an unresolved label. |

`TEST_EXECUTION_TIMEOUT` is a semantic test-execution outcome, not an Agent
timeout, container-start failure, Slurm walltime, or patch-application error.
The evaluator intentionally records it as unresolved for an individual PCE
workflow outcome, but a labelled validation snapshot uses only parsed outcomes
and therefore excludes it.

## Included set and operational audit

The resulting cleaned membership contains 111 cases. For all 112 repaired
Evaluate tasks:

- Code patch preflight and application succeeded once, with no fallback;
- test patch preflight and application succeeded once, with no fallback;
- 111 test commands terminated and produced parsed official evidence;
- no return code 126/127, host-home leakage, SIF error, or permission failure
  was observed;
- two Slurm allocations ended `OUT_OF_MEMORY` after a durable evaluator output
  had already been atomically saved. One was the semantic timeout above and the
  other had a parsed result. These scheduler events remain raw audit evidence
  but do not remove the already-complete parsed case.

The 111-member identity is the same as the earlier
`20260815_python111_testparsed_26dad63b5cf3` snapshot, but labels from that
snapshot remain non-authoritative because its old evaluator was contaminated.
Future PCE/PCCE comparisons must use the repaired evaluator outcomes as the
label authority while retaining this fixed membership.
