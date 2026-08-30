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
- Retain the 141 eligible Stage-2 slices as the annotation universe and label
  immediate developer behavior toward P1: the 57 matched approvals are ACCEPT
  and the 84 matched rejections are DO_NOT_ACCEPT.
- Exclude the ten cases whose two repositories remained unavailable after
  authenticated recovery. The repository-available candidate pool is 131
  cases: 54 ACCEPT and 77 DO_NOT_ACCEPT.
- Do not treat the canonical checkpoint's first-commit parent as the general P1
  base. The frozen audit verifies only two ACCEPT cases; conservative structured
  `Write`/`Edit` replay adds none because all eight candidates have additional
  opaque worktree effects.
- Use the separately frozen temporal repository policy for Behavioral v1. All
  131 repository-ready cases have an explicitly approximate source commit
  strictly before the session boundary: 67 from the retained recorded branch
  and 64 from ordinary source refs. Entire-managed refs and known current-
  session commits/descendants are excluded. Captured pre-P1 tool results are
  authoritative when they conflict with the proxy.
- Optimize high-confidence `ACCEPT` versus `DO_NOT_ACCEPT` labels.
- Preserve ambiguous cases for analysis but exclude them from v1 GEPA
  optimization.
- Keep per-example 0/1 candidate scores and rich controlled Reflection evidence.
- Keep the fixed Checker prompt minimal; the candidate guideline owns the
  review method and software-engineering reasoning.
- Use the neutral initial candidate guideline: “Evaluate whether the proposed
  plan should be accepted for implementation based on the information
  available at the time of the decision.” This seed is not fixed prompt text.
- Adapt through dataset/config/task semantics before changing GEPA search logic.

## Open design issues

1. Classify rejection reasons as controlled explanatory evidence without
   changing the 84 observed DO_NOT_ACCEPT labels.
2. Define treatment of rejection/revision feedback, later Plans, silence,
   topic change, and implementation behavior in controlled Reflection evidence.
3. Freeze the real session/task/repository/near-duplicate split with
   deterministic leakage checks. The implemented source-to-snapshot builder
   requires a complete exact-universe split manifest, and the loader enforces
   the final Checker boundary and split-disjoint IDs.
4. Wire the implemented temporal-proxy materializer and Behavioral Adapter into
   the local Checker, runner, Offline worker, retry/audit path, and resume
   identity without changing third-party GEPA.
5. Decide whether accuracy remains the v1 search metric and which diagnostics
   are mandatory: rejection precision, bad-plan recall, balanced accuracy, MCC,
   class prevalence, and confusion matrix.
6. Predeclare data volume, class balance, minibatch, budget,
   stopping conditions, and acceptance criteria before any LLM run.
7. Manually audit the frozen first-Plan projections and complete the remaining
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
