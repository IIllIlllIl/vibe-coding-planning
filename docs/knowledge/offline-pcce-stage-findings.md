# Offline Guideline PCCE Stage Findings

> Knowledge status: frozen interpretation of the first clean PolyBench PCCE
> stage; not runtime authority
>
> Last reviewed: 2026-08-28

## Scope

This note closes the first deployment-oriented evaluation of the Offline GEPA
guideline. It compares the no-Checker PCE baseline with PCCE using the minimal
default-accept seed and frozen minibatch-eight candidate 2 (`C2`). It preserves
the evidence needed to design the next experiment without treating PolyBench
as new GEPA training data.

The authoritative paired input is
`20260826_python99_cleanpce_depcache_03619730229d`: 99 cases whose PCE first
plans and outcomes were produced from clean, verified `base_commit`
workspaces. Its PCE result is 70 resolved and 29 unresolved. The Seed and C2
PCCE runs use those exact first plans. Their dependency-risk Evaluate cases
were subsequently rerun with the same frozen cache and disabled-network
policy as PCE; the overlay changes only evaluator evidence, never Checker,
Planner, plan, Code, or patch evidence.

PolyBench remains external evidence. None of the results below may enter the
existing GEPA candidate tree, Reflection evidence, or retrospective guideline
repair while retaining a held-out claim.

## Evidence Authorities

- paired PCE input and outcome authority:
  `output/SWE-PolyBench/polybench-guideline-validation-datasets/20260826_python99_cleanpce_depcache_03619730229d/`;
- Seed method outcomes and repair:
  `output/SWE-PolyBench/polybench-pcce-runs/formal/seed-python99-clean-pce-v1-20260826/`;
- C2 method outcomes and repair:
  `output/SWE-PolyBench/polybench-pcce-runs/formal/b8-candidate2-python99-clean-pce-v1-20260826/`;
- frozen guideline text and SWE metrics:
  `configs/frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/`.

Seed already has a materialized comparison below its run directory. C2's
corrected counts in this note are a deterministic instance-ID overlay of its
immutable `pcce_outcomes.jsonl` with the 20 rows in
`evaluator_repairs/clean-depcache-v1-20260826/raw_evaluator_outcomes.jsonl`.
The overlay replaces only `evaluator_result.evaluator_resolved`; the parent and
repair files remain the data authorities.

## Primary End-to-End Result

| Method | Method-complete | Resolved | Unresolved | Operationally incomplete |
|---|---:|---:|---:|---:|
| PCE, no Checker | 99/99 | 70 | 29 | 0 |
| Seed PCCE, corrected overlay | 99/99 | 66 | 33 | 0 |
| C2 PCCE, corrected overlay | 98/99 | 66 | 32 | 1 |

C2's incomplete case is `huggingface__transformers-26164`; its second PC wave
exhausted workflow attempts without producing a worker output. It has no Code
or Evaluate result and must not be converted into an unresolved label. On the
98 cases with terminal results for all three methods, PCE resolves 69, Seed
resolves 66, and C2 resolves 66.

The first PCCE stage therefore provides no evidence that either Checker-guided
workflow improves end-to-end resolution over no-Checker PCE. C2 also provides
no end-to-end improvement over the minimal seed on the common terminal set.
This is a negative result for the tested workflow, not proof that plan review
or Offline GEPA cannot work.

## Conditional Behavior Audit

For a behavior-focused sensitivity analysis, three cases are removed from the
98-case terminal intersection:

- `huggingface__transformers-22458`: Seed exhausted the fixed three-rejection
  experimental limit;
- `huggingface__transformers-27114`: Seed produced an empty implementation;
- `langchain-ai__langchain-4420`: Seed and C2 produced an empty implementation,
  and the benchmark cannot represent a correct no-change answer as success.

This leaves 95 cases. The exclusions are defined by workflow semantics, not by
which guideline made a wrong prediction. They are useful for diagnosing
Checker behavior but are not a replacement primary endpoint.

| Method | Resolved | Rate |
|---|---:|---:|
| PCE | 67/95 | 70.53% |
| Seed PCCE | 66/95 | 69.47% |
| C2 PCCE | 65/95 | 68.42% |

Using PCE resolved/unresolved as an operational proxy for whether the frozen
first plan should proceed, and treating a bad plan/rejection as positive:

| Guideline | Reject bad (TP) | Reject good (FP) | Accept good (TN) | Accept bad (FN) | Rejection precision | Bad-plan recall | Classification accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|
| Seed | 5 | 5 | 62 | 23 | 50.0% | 17.86% | 70.53% |
| C2 | 4 | 9 | 58 | 24 | 30.77% | 14.29% | 65.26% |

This proxy is intentionally weaker than a plan-quality label: a capable fresh
Code Agent can repair an inaccurate plan, and a correct plan can still fail
because of Code sampling or evaluator noise. It nevertheless exposes how the
guidelines changed the actual deployment decision.

Seed rejected 10 cases and C2 rejected 13. Their intersection contains only
six cases (Jaccard 35.3%), so C2 did not merely make Seed stricter. Relative to
Seed, C2 accepted four Seed-only rejections and introduced seven new
rejections; those changes reduced correct proxy classifications by five in
total. The learned guideline's external reject set is therefore unstable and
less precise, despite C2 improving SWE validation accuracy from 68/98 to 74/98.

## Intervention Result

Neither guideline repaired a PCE-unresolved case to resolved:

| Guideline | Rejected cases | Resolved→unresolved | Unresolved→resolved | Net change |
|---|---:|---:|---:|---:|
| Seed | 10 | 1 | 0 | -1 |
| C2 | 13 | 1 | 0 | -1 |

The C2 audit found four cases where rejection correctly identified a bad PCE
plan but the revised plan and fresh Code execution still failed. Typical
patterns were:

- the feedback named one valid blocker but omitted another API or compatibility
  contract;
- the Planner repaired the named issue while dropping an existing invariant;
- the replacement made no effective implementation change;
- the next Checker review verified feedback closure rather than rebuilding the
  complete requirement model.

Across the 13 C2-rejected cases, revised plans remained in the same code locus:
all fresh patches touched the same file set as the corresponding PCE patch.
The failure is better described as feedback anchoring and incomplete repair
specification than broad task migration.

## Dominant Failure Patterns

The 24 C2 false negatives in the 95-case audit—accepted plans whose PCE and
fresh C2 execution were both unresolved—were manually assigned one primary
diagnosis:

| Primary diagnosis | Cases | Interpretation |
|---|---:|---|
| Compatibility or boundary omissions | 16 | The plan found the local edit but missed variants, negative paths, integration hooks, API shapes, or behavior that must remain unchanged |
| Requirement or root-cause incomplete | 3 | The plan targeted a plausible symptom without reconstructing the actual acceptance contract |
| Direct Code implementation error | 1 | The plan was not the main observable cause of failure |
| Evaluator/environment or unrelated noise | 4 | The outcome is weak evidence about plan quality |

The nine C2 false positives—rejected plans whose no-Checker PCE executions
resolved—show a complementary problem. Seven contain real plan inaccuracies,
but the fresh Code Agent could inspect the repository, adapt the pseudo-code,
and solve the task. Under the intended permissive policy of rejecting only
clearly blocking plans, these are still operational false positives. C2 often
treated an inaccurate implementation detail as proof that the plan could not
succeed.

Plan length and detail did not separate success from failure. Accepted but
unresolved C2 plans were not systematically shorter than accepted resolved
plans. The recurring issue was missing or incorrect requirements, invariants,
and boundary behavior, not simply insufficient prose.

## What This Stage Establishes

1. **The platform is usable.** Clean per-Agent repositories, Agent-owned staged
   implementation patches, phase checkpoints, paired first plans, independent
   workflow retries, and frozen evaluator dependency overlays now produce
   auditable evidence.
2. **The tested method is not yet useful.** Seed and C2 both underperform PCE,
   and neither repairs a failing first plan in the audited intersection.
3. **SWE classification improvement did not transfer.** C2's six-case SWE
   gain was mainly fewer false rejections of resolved plans, while its
   PolyBench reject set became less precise. The SWE validation split was also
   repeatedly used by GEPA selection and is not an untouched final holdout.
4. **Classification and repair are different tasks.** Detecting one defect can
   justify a rejection, but a Planner needs a sufficiently complete causal and
   compatibility account to produce a better replacement plan.
5. **More guideline detail alone is not the demonstrated bottleneck.** The
   stronger hypothesis is insufficient independent requirement reconstruction,
   combined with treating the first identified blocker as a complete repair
   specification.

## Decision And Requirements For The Next Design

This PCCE method-quality stage is paused. Do not launch candidate 6 or reserve
guidelines under the unchanged workflow merely to search for a better
PolyBench result. Existing Seed/C2 text, trajectories, decisions, feedback,
plans, patches, evaluator repairs, and operational failures remain frozen for
analysis.

Before a new confirmatory experiment, the design should separately specify and
measure:

- first-review classification, especially rejection precision and bad-plan
  coverage;
- feedback correctness versus feedback completeness;
- revised-plan preservation of valid prior requirements and invariants;
- a fresh review of the entire replacement plan rather than only closure of
  the previous feedback;
- end-to-end intervention benefit on a predeclared common terminal set;
- operational/environment/empty-generation exclusions and sensitivity
  reporting;
- an untouched final holdout after any design informed by these PolyBench
  findings.

The existing PCCE implementation may be reused as execution infrastructure,
but its current prompts and three-review method must not be treated as the
accepted next experimental design merely because the workflow runs reliably.
