# Current Research Decisions And Issues

> This branch records current decisions and open risks only. Historical
> timelines remain available from
> `main@95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1`.

## Current state

The first clean PolyBench PCCE stage is complete and paused. PCE resolves 70/99
cases, Seed PCCE resolves 66/99, and candidate 2 has 66 resolved, 32 unresolved,
and one operationally incomplete case. On the common 98-case terminal
intersection, PCE / Seed / C2 resolve 69 / 66 / 66. Neither guideline repaired a
PCE-unresolved case in the separate 95-case behavior audit.

Therefore:

- do not launch candidate 6, reserve guidelines, check-only, or another run of
  the unchanged PCCE prompt/three-rejection method;
- do not feed PolyBench results into the existing GEPA candidate tree;
- retain the Offline/PCE/PCCE platform, raw evidence, checkpoints, evaluator
  repairs, and reproduction semantics;
- design Behavioral Plan Acceptability before running a new experiment.

No new LLM, GEPA, Docker, Apptainer, HPC, PCE, PCCE, check-only, or OpenCode run
is currently authorized.

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

1. Locate and verify the SWE-chat data source, schema, license, and snapshot
   identity without guessing.
2. Define a deterministic Plan Decision Episode and the first-clean-episode
   rule.
3. Define high-precision evidence for acceptance, rejection/revision, ambiguous
   clarification, silence, topic change, continued implementation, tool error,
   and context interruption.
4. Specify Checker-visible and Reflection-only schemas with a testable
   no-leakage boundary.
5. Determine whether repository state and base commit can be reconstructed
   reliably for each episode.
6. Define session/task/repository/near-duplicate split and leakage checks.
7. Select the smallest adapter from the new case schema to the existing
   Checker, Adapter, Reflection, and runner.
8. Decide whether accuracy remains the v1 search metric and which diagnostics
   are mandatory: rejection precision, bad-plan recall, balanced accuracy, MCC,
   class prevalence, and confusion matrix.
9. Predeclare data volume, class balance, minibatch, seed guideline, budget,
   stopping conditions, and acceptance criteria before any LLM run.
10. Build and audit the dataset path without LLM calls before smoke or formal
    optimization.

## Branch engineering plan

Phase 1 establishes this focused documentation surface and a separately
runnable no-LLM Offline regression suite. A follow-up surface cleanup archives
unambiguously historical Online/PCT documents, configs, and operator scripts,
without deleting historical source modules or frozen evidence.

Phase 2 implements the minimal Behavioral data skeleton and its deterministic
tests in commits separate from branch cleanup.

Phase 3 records import/reachability evidence and removes unreachable Online and
older-method source paths in a dedicated cleanup commit. Existing Offline GEPA
and frozen PolyBench reproduction tests must remain green before and after that
cleanup.

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
