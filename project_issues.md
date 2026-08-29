# Current Research Decisions And Issues

> This branch records current decisions and open risks only. Historical
> timelines remain available from
> `main@95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1`.

## Current decision constraints

The frozen evidence behind these constraints is maintained in
`docs/knowledge/offline-pcce-stage-findings.md`; the frozen Behavioral data
funnel and source-quality findings are maintained in
`docs/swe-chat-data-cleaning.md`. They are not duplicated here as live status.

Current decisions are:

- do not launch candidate 6, reserve guidelines, check-only, or another run of
  the unchanged PCCE prompt/three-rejection method;
- do not feed PolyBench results into the existing GEPA candidate tree;
- retain the Offline/PCE/PCCE platform, raw evidence, checkpoints, evaluator
  repairs, and reproduction semantics;
- require an explicit user instruction and a new frozen contract before any
  new experiment is launched.

## Accepted Behavioral v1 direction

- Use at most the first clean Plan Mode episode from each SWE-chat session.
- Treat later plan revisions as post-boundary diagnostic evidence, not separate
  v1 classification examples.
- Optimize high-confidence `ACCEPT` versus `DO_NOT_ACCEPT` labels.
- Preserve ambiguous cases for analysis but exclude them from v1 GEPA
  optimization.
- Keep per-example 0/1 candidate scores and rich controlled Reflection evidence.
- Keep the fixed Checker prompt minimal; the candidate guideline owns the
  review method and software-engineering reasoning.
- Adapt through dataset/config/task semantics before changing GEPA search logic.

## Open design issues

1. Freeze the final high-precision mapping from observed approval/rejection
   evidence to ACCEPT/DO_NOT_ACCEPT labels and retain ambiguous cases.
2. Define treatment of rejection/revision feedback, later Plans, silence,
   topic change, and implementation behavior in controlled Reflection evidence.
3. Map the frozen Checker-visible and Reflection-only case schema into the
   Offline adapter while retaining a testable no-leakage boundary.
4. Determine whether repository state and base commit can be reconstructed
   reliably for each episode.
5. Define session/task/repository/near-duplicate split and leakage checks.
6. Select the smallest adapter from the new case schema to the existing
   Checker, Adapter, Reflection, and runner.
7. Decide whether accuracy remains the v1 search metric and which diagnostics
   are mandatory: rejection precision, bad-plan recall, balanced accuracy, MCC,
   class prevalence, and confusion matrix.
8. Predeclare data volume, class balance, minibatch, seed guideline, budget,
   stopping conditions, and acceptance criteria before any LLM run.
9. Manually audit the frozen first-Plan projections and complete the remaining
   no-LLM dataset schema before smoke or formal optimization.

## Known validity constraints

- Historical `resolved` is not a direct plan-quality label.
- Developer continuation into implementation is not automatically strong plan
  endorsement.
- The repeatedly selected SWE 98-case validation split is not an untouched
  final holdout.
- Classification, feedback completeness, revised-plan quality, and end-to-end
  intervention benefit are separate claims.
- PolyBench findings may motivate the new design but cannot support a new
  untouched-generalization claim without a different final holdout.
- Operationally incomplete outcomes must not be coerced into research labels.
