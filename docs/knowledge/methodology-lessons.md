# Methodology Evolution And Reusable Lessons

> Knowledge status: rationale, not runtime authority
> Last reviewed: 2026-07-22

## Evolution

| Method | Contribution | Limitation |
|---|---|---|
| PCT | Real Plan-Code-Test trajectories and execution failure evidence | Did not directly optimize one deployable global rule set |
| PCC/Checker | Structured plan-quality diagnosis and held-out classification | Prediction of historical resolved is indirect relative to actual execution |
| Offline GEPA | Active low-cost route for learning an inspectable, standalone plan-review guideline that covers both repository investigation and judgment; reuses historical execution evidence for Reflection | The frozen 20260731 design placed too much investigation knowledge in the fixed Checker and optimized a narrower checklist. It also optimizes Checker agreement with historical labels, so label/Checker bias and transfer to actual plan quality require held-out analysis |
| Online GEPA | Optimizes rules through current Plan-Code-Evaluator rollouts | Expensive and sensitive to Agent/evaluator/infrastructure noise |

## What Online Reuses

- PCT step limits, command timeout distinctions, and full trajectories.
- PCT phase isolation and clean repository semantics.
- Checker structured failure attribution, without using Checker prediction as
  the optimization objective.
- Offline GEPA candidate/Pareto/checkpoint machinery and Reflection templates.
- HPC pilots' resource, SIF cache, FairShare, and resume evidence.

## What Online Rejects

- Historical resolved labels as current metric results.
- Historical plans/patches/ASI as Plan or Code inputs.
- Checker score as a substitute for official evaluator outcome.
- Cross-policy score comparisons.
- Treating infrastructure failure as unresolved.

Historical documents remain useful for provenance. They are not authoritative
for current behavior; current Offline semantics are restated in
[`../offline-gepa.md`](../offline-gepa.md).
