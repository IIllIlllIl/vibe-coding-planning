# Initial Behavioral GEPA Findings

> Authority: frozen findings from the first SWE-chat Behavioral Plan
> Acceptability v1 optimization and its first small PolyBench PC-only
> diagnostic

## Scope

The formal run optimized a plan-review guideline against observed developer
behavior at the first Plan decision boundary. Its 84 training cases and 47
validation cases are repository-disjoint, but validation participated in GEPA
candidate selection and is not an untouched holdout. The labels are behavioral
`ACCEPT` / `DO_NOT_ACCEPT` observations, not objective plan-quality or
downstream implementation-success labels.

The run used the frozen media-projected 131-case snapshot, neutral seed,
accuracy search metric, minibatches of eight, and eight proposal iterations.
All Checker inputs ended at P1 and used an explicitly approximate pre-session
repository proxy; post-boundary evidence was available only to Reflection.

## Formal Run Result

The run completed all eight proposal iterations with eight successful
Reflection proposals, five accepted candidates, 410 logical metric calls, and
no operationally incomplete validation prediction. Six candidates, including
the seed, received a full 47-case validation evaluation.

| Candidate | Accuracy | Balanced accuracy | MCC | ACCEPT recall | DO_NOT_ACCEPT recall | ACCEPT rate |
|---:|---:|---:|---:|---:|---:|---:|
| 0 (neutral seed) | 42.6% | 47.4% | -0.052 | 62.5% | 32.3% | 66.0% |
| 1 | 59.6% | 55.7% | 0.113 | 43.8% | 67.7% | 36.2% |
| 2 | 55.3% | 61.6% | 0.232 | 81.2% | 41.9% | 66.0% |
| 3 | 51.1% | 53.8% | 0.073 | 62.5% | 45.2% | 57.4% |
| 4 | 59.6% | 58.8% | 0.167 | 56.2% | 61.3% | 44.7% |
| 5 | 38.3% | 44.2% | -0.121 | 62.5% | 25.8% | 70.2% |

GEPA records candidate 1 as `best_idx=1` because candidates 1 and 4 tie on the
configured primary metric at 28/47 and candidate 1 is the first maximum. This
does not establish candidate 1 as best under a broader utility function.
Candidate 4 has the stronger balanced accuracy, MCC, and balance between the
two class recalls among the tied candidates. The user therefore selected
candidate 4 for the first downstream diagnostic. This is a post-run selection
decision and must not be retroactively described as the GEPA primary-metric
winner.

The result establishes that the Behavioral information flow and Offline GEPA
adaptation can complete a bounded search and produce nontrivial candidate
guidelines. It does not establish generalization, objective plan quality, or
intervention benefit.

## PolyBench C4 PC-Only Diagnostic

The first external diagnostic reused the frozen clean-PCE PolyBench authority
but did not run Planner, Code, or Evaluate. A conservative policy removed four
unresolved cases dominated by directly evidenced evaluator/environment noise;
the existing high-precision placeholder rule removed none. Deterministic
repository-stratified sampling then selected 10 resolved and 10 unresolved
cases from the remaining 91-case conditional audit universe.

All 20 C4 Checker calls produced valid binary decisions with no operationally
incomplete outcome. On this deliberately balanced development subset:

| Guideline | Accuracy | Balanced accuracy | Bad-plan recall | Rejection precision |
|---|---:|---:|---:|---:|
| Seed | 60% (12/20) | 60% | 30% (3/10) | 75% (3/4) |
| C4 | 65% (13/20) | 65% | 50% (5/10) | 71.4% (5/7) |

C4 intercepted two additional historically unresolved plans while rejecting
one additional historically resolved plan. The sample is small, balanced by
construction, and selected after C4 was observed on SWE-chat validation. It is
a directional external diagnostic, not an estimate of PolyBench prevalence or
an untouched generalization result. Historical `resolved` is also not the same
construct as developer acceptance, so the comparison tests transfer of
decision behavior rather than label equivalence.

## Implications For The Next Stage

- Candidate choice should be predeclared with a multi-metric rule when the
  research objective values both classes; accuracy alone selected the first
  member of a tie and hid materially different class behavior.
- The next evaluation needs an independent, frozen selection and success
  contract before any additional candidate result is inspected.
- Rejection reason and Plan-revision evidence remain useful for qualitative
  analysis, but must not change the frozen behavioral labels or enter Checker
  input.
- A larger PolyBench study must keep PC classification separate from PCCE
  intervention benefit and from downstream resolution.

## Artifact Authority

- Formal config:
  `configs/gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml`
- Formal remote run:
  `/scratch/users/twang/vibe-coding-planning/behavioral-gepa-run-state/output/SWE-chat/behavioral-gepa-runs/formal/repository-holdout-media-projected-accuracy-b8-8it-v2-20260830`
- Frozen local all-candidate result bundle:
  `configs/frozen_guidelines/behavioral-formal-all-candidates-v1-20260831/`
- Frozen C4 text and provenance:
  `configs/frozen_guidelines/behavioral-formal-c4-v1-20260831/`
- Frozen PolyBench diagnostic selection:
  `configs/frozen_polybench_pc_quick/c4-balanced20-v1-20260831.json`
- PolyBench PC-only remote run:
  `~/hpc_run_state/vibe-coding-planning/output/SWE-PolyBench/polybench-pc-checker-only-runs/development/c4-balanced20-v1-20260831`
