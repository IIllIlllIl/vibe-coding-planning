# ACE + Safe PCE Branch Scope

> Authority: active-versus-historical boundary for
> `research/behavioral-plan-acceptability-v1`
>
> Baseline: `main@95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1`

## Purpose

This branch now develops an ACE-style reject playbook and a trustworthy Safe
PCE evidence source. The intended artifact remains a transferable,
human-readable checklist for deciding whether a proposed software plan should
cross into implementation, followed by ACE-PCCE evaluation.

The branch is ordinary Git history, not an orphan snapshot. Deleted or later
pruned material remains readable from `main` and the baseline commit.

## Active systems

The active implementation and evidence surface is limited to:

1. ACE reject-playbook optimization: repository-free per-bullet Checker,
   structured Reflector, Curator, global counters, deterministic validity and
   length gates, and GEPA candidate/checkpoint machinery.
2. Safe PCE on SWE-bench Verified: direct Plan submission, phase isolation,
   exact Plan-to-Code handoff, conservative Agent acquisition boundary, and the
   official evaluator.
3. The next ACE-PCCE adapter, after Safe PCE produces trustworthy plans and
   outcomes.
4. Shared Agent, repository, container, evaluator, Slurm, audit, and output
   utilities actually reached by those systems.
5. Historical Offline, Behavioral, PolyBench, quick50, C2/C4/C5, and safe67
   artifacts only when diagnosing earlier failure modes or reusing a tested
   execution component.

Existing Offline, PolyBench, quick50, and C2/C4/C5 semantics are historical
reproduction and failure-analysis authorities, not new experiment inputs or
baselines. New work must use a new dataset identity,
config, run directory, prompt identity, and acceptance contract.

## Behavioral information boundary

A Plan Decision Episode has a strict decision boundary:

```text
pre-boundary: task/context + repository state + proposed plan
post-boundary: developer reaction + revisions + later trajectory
```

Only pre-boundary fields may enter Checker-visible input. Post-boundary evidence
may determine a high-confidence `ACCEPT` or `DO_NOT_ACCEPT` label and may be
available to GEPA Reflection. Ambiguous episodes remain auditable but are
excluded from v1 optimization. The first implementation must use hard labels
and per-example 0/1 candidate scores; soft labels and confidence weighting are
out of scope.

## Archived surface And Temporarily Retained Code

Standalone Online GEPA/PCT/old-analysis authority documents, top-level configs,
and Online resource-pilot scripts are behind `docs/archive/`,
`configs/archive/`, and `scripts/archive/`. This reduces ordinary search noise
without deleting their history.

Online GEPA, PCT/PCC, Pro, and old analysis source modules remain temporarily
present. Mixed shared entrypoints also remain when moving them would require
code-level dependency decisions. These paths are not active research authority
and should not appear in ordinary Agent searches or new design citations.

Broad pruning begins only after all of the following exist:

- a minimal Behavioral case model and deterministic loader;
- an executable first-clean-episode rule;
- Checker-visible and Reflection-only schemas;
- high-confidence label and ambiguous-exclusion policy;
- no-leakage and split/deduplication tests;
- an import/reachability report from the retained Offline, PCE/PCCE, and new
  Behavioral entry points.

Further code pruning must be a separate commit from Behavioral implementation.

## Frozen evidence

The first clean PolyBench stage remains frozen:

- paired PCE: 70 resolved / 29 unresolved;
- Seed PCCE: 66 resolved / 33 unresolved;
- candidate 2 PCCE: 66 resolved / 32 unresolved / 1 operational incomplete;
- common 98-case terminal intersection: PCE / Seed / C2 = 69 / 66 / 66.

These results motivate redesign but must not be fed into the existing GEPA
candidate tree. Frozen inputs, trajectories, checkpoints, guideline text, and
evaluator repair evidence must not be modified in place.

## Historical access

Prefer an explicit read from the baseline over restoring historical files:

```bash
git show main:<path>
git show 95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1:<path>
git diff 95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1...HEAD -- <path>
```

Archive browsing is allowed only for an explicitly scoped audit, comparison,
or reproduction task.

## Worktree-local data references

Git tracks the output catalog but not the large frozen datasets and raw run
trees. The independent worktree therefore uses ignored local references to the
existing `output/SWE-bench_Verified` and `output/SWE-PolyBench` roots in the
main worktree. These references allow reproduction tests to read the frozen
evidence without copying or changing it. They must never be staged or treated
as branch-owned data.
