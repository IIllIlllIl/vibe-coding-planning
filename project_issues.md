# Current Research Decisions And Issues

> This branch records current decisions and open risks only. Historical
> timelines remain available from
> `main@95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1`.

## Current decision constraints

The active stage is ACE + Safe PCE. Old Offline GEPA, Behavioral C4/C5,
quick50, safe67, and PolyBench outcomes may explain prior failures and motivate
new safeguards, but must not supply cases, labels, image manifests, candidate
selection, comparison baselines, or launch identity to the new experiment.

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

## Accepted ACE playbook direction

The next method is specified in
`docs/offline-gepa-playbook-redesign.md`. Its deterministic method and
distributed Slurm execution path are implemented and contract-tested. Terminal
development findings that changed the method remain in that document; live run
state remains in runtime artifacts.

- Keep the task as cost-sensitive classification; do not add Replan, Code, or
  Evaluate phases to candidate evaluation.
- Optimize a human-readable `Reject the plan when:` playbook whose bullets
  each express one Plan failure pattern.
- Do not let the Checker inspect a repository. It sees only the issue, Plan,
  temporary bullet numbers, and bullet text; stable IDs and helpful/harmful
  counters remain Reflection-only metadata.
- Have the Checker classify every bullet, then derive the Plan decision in the
  host: any trigger means REJECT and no trigger means ACCEPT.
- Use historical `RESOLVED` / `UNRESOLVED` as the explicitly scoped
  implementation-success proxy and score ACCEPT/REJECT on Good/Bad as
  `0/-1/-5/0`; invalid output scores `-100`.
- Replace single-stage Reflection with one structured per-case Reflector and
  one cross-case Curator. The Reflector assigns helpful, neutral, or harmful
  tags from trajectories and outcome evidence; the host updates counters and
  the Curator proposes local playbook changes.
- Trigger length management only when the Checker-visible projection exceeds
  10,000 frozen-tokenizer tokens. An Agent may deduplicate, compress, and merge;
  any remaining counter-based whole-bullet pruning is deterministic and never
  delegated to an LLM.
- The eligibility-cleaned Verified derivative, prompt bundle, and distributed
  execution contract do not themselves authorize an experiment launch.

## Historical Behavioral v1 decisions

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
- For the first development smoke, describe the repository proxy only as an
  approximate pre-session checkout, make conflicting pre-decision transcript
  observations authoritative, and otherwise permit the proxy as supplementary
  evidence. Do not encode proxy distrust as a fixed review strategy.
- Call Reflection supervision `observed_decision` and `observed_accept`; these
  are developer-behavior observations, not objective plan-quality truth.
- Use the neutral initial candidate guideline: “Evaluate whether the proposed
  plan should be accepted for implementation based on the information
  available at the time of the decision.” This seed is not fixed prompt text.
- Adapt through dataset/config/task semantics before changing GEPA search logic.
- Validate the first prompt/runtime path in ordered stages: local no-LLM
  contracts, bounded local Checker/Reflection units, then one full HPC GEPA
  proposal on a separate balanced 8-case development fixture. Smoke metric
  improvement is not an acceptance requirement.
- Stage C v2 satisfied that flow contract: one proposal iteration, 16 logical
  metric calls, zero incomplete decisions, zero audited Checker leakage, and
  complete raw evidence/reporting. Seed and proposal both scored 0.5 on the
  development validation cases, so this is platform evidence only.
- The formal split is label-independent and repository-disjoint. Every
  repository/duplicate component containing a smoke-exposed case enters train;
  all remaining components enter validation. The result is 84 train cases
  across eight repositories and 47 validation cases across 29 repositories.
  Formal validation is candidate-selection data, not an untouched holdout.
- The media-projected formal accuracy/batch-eight/eight-iteration v2 config and
  snapshot superseded v1 before launch. They omit structured image base64 only
  from Checker text while preserving hashed descriptors and the frozen raw
  authority. The extrema smoke passed and the authorized formal run completed
  all eight proposal iterations with no incomplete validation prediction.
- Treat candidate 1 as the GEPA primary-metric winner by first-maximum
  tie-breaking, not as an unqualified overall winner. Candidate 4 tied its
  accuracy and was selected for the first external diagnostic because its
  balanced accuracy, MCC, and class-recall balance were stronger.
- Treat the completed 20-case C4 PolyBench PC-only comparison as a directional
  development diagnostic. It is neither an untouched holdout nor evidence of
  downstream intervention benefit.

## Open design issues

1. Audit any future formal/held-out split separately. The eligibility-cleaned
   444-case snapshot preserves the historical development split and is not a
   held-out generalization authority.
2. Continue monitoring outcome authority, exact/near duplicates and repository leakage,
   placeholders, and `/tmp` or other transient-state dependencies. The last
   category must account for the historical topology mismatch where the PCE
   Planner and Coder could share `/tmp` but the PCCE Checker could not observe
   the same directory. This is eligibility cleaning, not construction of a new
   test set; retain an internal GEPA train/validation role only for search and
   selection. PolyBench is the planned external evaluation dataset. Any later
   eligibility-policy change requires a new frozen snapshot identity.
3. Preserve the new deployment boundary: the Checker does not access a
   repository and sees neither stable IDs nor counters. Historical labels,
   trajectories, patches, evaluator evidence, and rule attribution are
   Reflection-only.
4. Predeclare a new development/validation/held-out split. The Behavioral
   47-case validation, safe67, balanced20, repair3, 24pcce, and safe-U8 cases
   are already development-exposed and cannot become the principal held-out
   evaluation of a method derived from them.
5. Define an intervention-benefit endpoint that distinguishes an actual
   rejection/revision-mediated U-to-R from a first-review ACCEPT followed by a
   different stochastic Code result. Report R-to-U and operationally incomplete
   outcomes alongside any repair.
6. Fix and regression-test the PolyBench PC-only CLI summary path: the
   controller writes a complete result, then `run_polybench_pcce_hpc.py`
   currently exits nonzero by reading the full-PCCE-only `method_outcomes`
   field.
7. The 25-image Pro audit confirmed that base checkouts expose the evaluator's
   gold commit and substantial non-ancestor history. An ancestor-only clone was
   rejected after the first PCE smoke showed that it discards official
   OpenLibrary submodules and build artifacts. Plan, Code, and Evaluate now use
   separate fresh official SIF workspaces without generic reset/clean; Plan and
   Code retain `--containall`, and observed Agent Git-history exploration makes
   a case `unknown`. Network policy remains unchanged. The Slurm audit had 21 valid
   inspections and four `dubious ownership` operational failures; the direct
   SquashFS audit remains the 25/25 authority. The ancestor-only
   three-repository smoke completed Ansible and qutebrowser but failed closed
   on the officially dirty OpenLibrary workspace, so it remains invalidated
   diagnostic provenance. The replacement official-workspace smoke completed
   3/3 cases, preserved OpenLibrary state, produced all nine phase baselines,
   and observed no Agent Git-history access. Quick25 is operationally
   authorized; latent history visibility remains a reported limitation.
8. Pro C4 PCCE is paused after all three review waves. Its CE wave retained 15
   terminal evaluator outputs at the pause boundary, but eight OpenLibrary
   workspaces exhausted quota while extracting their large prepared
   `node_modules` trees, and failure-artifact writes hit the same quota. The
   local supervisor is stopped and already-submitted work was not cancelled.
   Before any resume, re-inventory the final CE state, move only disposable
   phase workspaces to a measured quota-safe location, smoke one affected
   OpenLibrary case, and preserve the existing semantic identity and completed
   checkpoints. Operational failures must not be counted as unresolved.

## Completed development evidence relevant to redesign

- SWE-Verified quick50 is complete: PCE 41/50, neutral Seed PCCE 37/50,
  and C4 PCCE 41/50. Seed introduced four R-to-U transitions; C4 preserved all
  PCE outcomes but produced no U-to-R.
- The original C4 safe67 analysis has 49 R-to-R and 18 U-to-U cases. It is a
  mechanism-development set, not future RQ3 evaluation data.
- PolyBench C5 repair3 produced one U-to-R and two U-to-U outcomes, but the
  sole U-to-R passed Review 1 and is not evidence of intervention benefit.
- PolyBench C5 24pcce ended with 10 resolved, 10 unresolved, and four
  operationally incomplete outcomes; its terminal cases contain no U-to-R.
- C5 safe-U8 selected all eight cases from known PCE failures. Four direct
  Review-1 accepts remained unresolved. Four frozen Review-1 rejections were
  recovered through revision and later review, producing two resolved and two
  unresolved outcomes. These two revision-mediated U-to-R cases are useful
  mechanism evidence but are too selected and too few for an effectiveness or
  generalization claim.

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
- SWE-Verified has no process-level Code or evaluator deadline. Slurm owns the
  45-minute worker walltime. Three evidenced Slurm timeouts after a durable
  Evaluate-start marker become `unknown`; other exhaustion remains operational.
  Paired comparison uses the PCE/Seed/C4 non-unknown intersection while
  retaining the full predeclared cohort and exclusion accounting.
