# Offline GEPA Reject-Playbook Redesign

> Status: deterministic method and distributed smoke path implemented; smoke
> prepared but not launched
>
> Scope: the next Offline GEPA method, replacing the current single-decision
> guideline proposal while retaining third-party GEPA search where practical

## Objective And Boundary

The next method remains a classification experiment. It does not add Plan
revision, Code generation, or a new evaluator execution to candidate scoring.
Its target artifact is a concise, human-readable list of observable Plan
failure patterns:

```text
Reject the plan when:
- ...
- ...
```

The intended user is a human or coding Agent reviewing a Plan without reading
the repository. General software-engineering behavior remains an Agent
capability; the optimized artifact contains only learned rejection rules.

The Checker may see the issue, the proposed Plan, and the rendered rejection
rules. It must not inspect a repository. Historical labels, implementation
trajectories, patches, evaluator results, rule identities, counters, and
Reflection analyses remain outside the Checker boundary.

Historical SWE-bench Verified `RESOLVED` / `UNRESOLVED` remains the explicitly
accepted operational proxy for whether implementation from the Plan was good
or bad. This matches the experiment's implementation-correctness objective but
is not reinterpreted as direct Plan-quality ground truth.

## Candidate Representation And Checker Projection

Internally, every active playbook bullet has:

- a stable unique ID;
- one rejection-pattern text;
- a helpful counter;
- a harmful counter;
- optional lineage needed to audit revision, merge, or retirement.

The internal representation is the checkpoint, resume, fingerprint, Curator,
and audit authority. Before Checker execution, the adapter deterministically
projects it into an ordered text list. The Checker sees only temporary ordinal
numbers and bullet text. Stable IDs, counters, and lineage are removed.

For example, the Checker-visible projection is:

```text
Reject the plan when:

Rule 1. The Plan is only a placeholder and contains no implementation
strategy.

Rule 2. The Plan leaves a central behavioral or implementation choice
unresolved.
```

Temporary rule numbers exist only to join Checker results back to the active
candidate. They are regenerated from the candidate order and are not stable
rule identities.

Each bullet must express one rejection pattern. The playbook must not grow into
a general repository-investigation procedure or a complete coding handbook.

## Checker Output And Deterministic Decision

The Checker returns one structured result for every temporary rule number. A
result records whether the rule was triggered, the exact issue/Plan evidence,
and a concise reason. The Checker does not directly submit an overall
ACCEPT/REJECT decision.

Host validation requires:

- exactly one result for every rendered rule;
- no missing, duplicate, unknown, or out-of-range rule number;
- a Boolean trigger value for every result;
- evidence and reasoning grounded only in Checker-visible input;
- no repository claims presented as observed facts.

The host derives the Plan decision without an LLM:

```text
one or more triggered rules -> REJECT
no triggered rules          -> ACCEPT
invalid result list          -> INVALID
```

This OR aggregation makes each triggered bullet independently sufficient to
reject the Plan. The complete per-rule result list is retained for attribution,
local updates, retrieval, and incremental adaptation.

## Candidate Score

The Plan-level score is cost-sensitive and maximized by GEPA:

| Host decision | Good (`RESOLVED`) | Bad (`UNRESOLVED`) |
|---|---:|---:|
| ACCEPT | 0 | -1 |
| REJECT | -5 | 0 |
| structurally INVALID candidate | -100 | -100 |

The fivefold false-rejection penalty encodes the deployment preference against
unfriendly repeated rejection. Operationally incomplete or authority-uncertain
cases must not be silently converted to `UNRESOLVED`; their eligibility and
handling are fixed by the future cleaned dataset contract.

`INVALID` is reserved for a candidate playbook that violates the frozen
playbook contract, including a bullet longer than 128 Checker-model tokens. It does not mean
that a Checker returned malformed JSON. Checker output-contract failures retain
the raw Agent completion, receive Host validation feedback, and retry as fresh
Agent attempts; exhaustion is operationally incomplete and is not scored.
An overlength candidate receives `-100` per evaluated case without invoking
the Checker.
The global 10,000-token trigger is counted with the configured Checker model's
tokenizer, not whitespace-delimited words; the same count is supplied to the
Refiner and used by deterministic pruning.

Negative score support, a perfect score of zero, reporting, and any GEPA
skip-perfect behavior require project-side contract tests. Accuracy, balanced
accuracy, the confusion matrix, and accept/reject rates remain reported
descriptive metrics; the table above is the candidate-selection objective.

## Two-Stage Reflection

Reflection is separated into per-case attribution and cross-case curation.
Only cases selected for the GEPA Reflection minibatch receive a Reflector run;
ordinary full-validation evaluation does not create Reflector calls.

### Stage 1: Per-Case Reflector

One Reflector reads one case's repository-free evidence bundle:

- issue and Plan;
- current internal playbook and Checker-visible projection;
- complete Checker trajectory and per-rule trigger results;
- host-derived Plan decision and score;
- frozen `RESOLVED` / `UNRESOLVED` proxy label;
- historical Plan and Code trajectories, patch, evaluator result, and outcome
  authority metadata available in the cleaned Reflection bundle.

The Reflector produces a structured case analysis with reasoning, error
identification, root-cause analysis, correct classification approach, key
insight, uncertainty, and one tag for every active bullet ID. The permitted
tags are `helpful`, `neutral`, and `harmful`. Their exact evidence requirements
belong in the future Reflector prompt and output contract.

The Reflector uses the trajectory and result to make the attribution; it does
not infer tags from the confusion cell alone. In particular, a trigger on an
`UNRESOLVED` case is not automatically helpful when its stated reason is
unsupported or unrelated to the observed implementation failure. Neutral tags
do not change counters.

The Reflector neither edits the playbook nor updates counters. Its structured
output and full trajectory are immutable proposal evidence.

Large trajectories are stored as separate files rather than interpolated into
one model request. As in the earlier Offline GEPA, the bundle is mounted
read-only into an isolated generic evidence environment and the Reflector reads
manifest-listed files on demand. No SWE repository is mounted, and the Checker
continues to receive neither this bundle nor any container/SIF information.

### Stage 2: Curator

The host first applies supported helpful and harmful increments. One Curator
then receives that counted playbook and all completed Reflector records for the
minibatch. It:

1. preserves, adds, revises, deletes, or semantically merges bullets;
2. checks the false-rejection risk of every proposed change;
3. emits localized `ADD`, `REVISE`, `DELETE`, or `MERGE` operations and a
   separate change analysis; the host validates and applies them.

Counter arithmetic is host-owned. A retained ID must preserve its complete
bullet record. A substantively revised or merged rule receives a new ID, starts
with zero counters, and records input IDs in lineage. The Curator cannot assign
or rewrite evidence counters.

The Curator must not expose IDs or counters in Checker-visible bullet text.
Rule-ID and counter inheritance for substantive revisions and merges must be
fixed before implementation so semantically different rules do not inherit
misleading evidence.

## Length Management

Length is measured deterministically on the Checker-visible projection. The
maximum is **10,000 tokens** under a tokenizer and version that must be frozen
in the implementation contract.

After Curator output:

1. If the rendered playbook is at most 10,000 tokens, no length-management
   Agent runs.
2. If it exceeds 10,000 tokens, a semantic Refiner removes duplication,
   compresses wording, and merges genuinely overlapping bullets. It must not
   add new rejection knowledge or inspect case trajectories.
3. The host renders and counts again.
4. If the result remains over the limit, the host deterministically removes
   whole bullets according to a frozen counter-based ranking until the limit is
   met. An LLM does not choose pruning targets and bullet text is never
   truncated.

The implemented pruning utility is `helpful - 5 * harmful`; the lowest value
is removed first. Ties remove higher-harmful, then lower-helpful, then longer,
then lexicographically earlier IDs. New bullets start at zero. Retained IDs
preserve their counters; merged bullets start at zero and record their source
IDs in lineage. The tokenizer and any protection period for new untested rules
remain to be frozen with the formal configuration. Every refinement, merge,
and deterministic removal must retain an audit record.

## Dataset Direction And Cleaning

The next dataset is a new immutable derivative of the historical 482-case
SWE-bench Verified snapshot, not an in-place edit. Cleaning is performed after
the new flow is implemented and before prompts or a formal run are frozen.

The audit has four declared axes:

1. **Historical outcome authority:** separate usable resolved/unresolved
   proxies from operational, evaluator/environment, workspace, empty-output,
   and uncertain cases. Non-terminal evidence is never changed into Bad.
2. **Duplicates and repository leakage:** inspect exact and near-duplicate
   Plans/issues, repeated templates, repository overlap, and any component that
   crosses the eventual split.
3. **Placeholders:** reconstruct the existing high-precision placeholder
   policy, including the intentionally retained unresolved placeholders, and
   prevent repeated templates from dominating learning or counters.
4. **Temporary-state and execution-topology confounding:** inspect cases whose
   Plan, Planner trajectory, or Code trajectory creates, references, or relies
   on files under `/tmp` (or equivalent Agent-local transient state). In the
   historical PCE topology, Planner and Coder could share `/tmp`, while the
   PCCE Checker could not necessarily observe that same directory. Such cases
   can therefore produce method-dependent evidence visibility rather than a
   genuine difference in Plan acceptability or implementation quality. The
   audit must record which phase created the state, which phases could observe
   it, whether the state was required for the implementation, and whether the
   historical outcome remains comparable across PCE and PCCE. Confounded or
   authority-uncertain cases must be excluded or separately stratified; they
   must not be silently labeled Bad.

The existing snapshot contains 384 train and 98 validation cases, with 315
resolved and 167 unresolved overall. It has 481 unique exact Plan strings; the
only exact duplicate pair is `django__django-13512` and
`django__django-16950`, both unresolved and both in train. All 11 repositories
in the existing validation split also occur in train, so the old split is not
repository-disjoint.

A read-only preliminary audit of the frozen 482 on 2026-09-09 found:

- 481 cases have a consistent Boolean evaluator authority, a one-instance
  report, and an applied patch. `psf__requests-1142` has an evaluator error
  (`Reversed (or previously applied) patch detected`), an empty report, and an
  unresolved label; it is an authority exclusion candidate rather than an
  ordinary Bad example.
- The exact duplicate pair above contains the identical placeholder `test
  content`. A third retained unresolved placeholder, `sympy__sympy-22080`, is
  `test`. All three are useful negative-pattern evidence, but the identical
  pair must be grouped or reduced so it does not receive double weight.
- The snapshot spans 12 repositories. Eleven appear in validation and every
  one of those also appears in train; only the single-case `pallets/flask`
  repository is train-only. The prior split therefore measures within-repo
  transfer, not repository generalization.
- All 482 Planner trajectories mention `/tmp/plan.md` because that was the
  historical Planner protocol; this boilerplate is not a confound by itself.
  Final Plan text mentions `/tmp` in 31 cases. A conservative path scan finds
  31 cases where Planner and Coder trajectories reference at least one same
  non-plan `/tmp` path (19 resolved, 12 unresolved), with 27 overlapping the
  final-Plan set. Their union is 35 cases. Temporary reproducer instructions
  may be benign, but the frozen v1 cleaning policy conservatively excludes the
  complete auditable queue because a Coder consuming Planner-created state
  without reconstructing it is not distinguishable mechanically.
- Previously confirmed workspace-confounded cases
  `django__django-16136` and `pylint-dev__pylint-4970` are both present as
  unresolved train cases. The latter is also in the `/tmp` path-overlap queue.

The resulting cleaning direction is conservative: exclude definite outcome
authority failures; group exact and accepted near duplicates before weighting;
manually adjudicate the 35-case temporary-state queue plus known workspace
confounds; and retain one copy of each useful unresolved placeholder pattern.
This stage is an eligibility cleaning pass, not construction of a new test
set. Retained cases may preserve their historical train/validation membership
for GEPA search and candidate selection, but that validation remains
within-repository development data and is not a held-out generalization claim.
The historical 482 is development-exposed and cannot become the future
principal held-out set.

For the next conservative selection draft, treating all 35 temporary-state
candidates as excluded (pending any later reinstatement), excluding
`psf__requests-1142`, excluding both known workspace confounds, and retaining
only the lexicographically first member of the exact duplicate pair removes 38
unique cases. The remaining pool has 444 cases: 293 Good and 151 Bad. Thus the
experiment can afford conservative exclusions while retaining the requested
300--400-case scale.

Astropy and Sphinx will not be carved out as a new repository-disjoint test
set. PolyBench is the planned downstream external evaluation dataset. Its
existing development exposure and any future overlap or selection effects must
still be reported accurately; calling it an untouched holdout would require a
separate audit and is not implied by this cleaning decision.

### Materialized eligibility-cleaned snapshot

The conservative policy was materialized without further size-based sampling
at:

`configs/frozen_swe_verified_playbook_gepa/20260909_clean444_25e5ce38271c/`

It preserves source split membership and contains 444 cases: 356 train and 88
validation, with 293 Good and 151 Bad overall. The 38 excluded instance IDs
and all overlapping reason codes are recorded in `exclusions.json`; the
exhaustive 482-row decision authority is `audit_ledger.jsonl`. The manifest
pins source, output, ledger, exclusions, and ordered-membership hashes.

The exclusions comprise one invalid historical evaluator authority, two
previously confirmed workspace confounds, one extra copy of an exact
normalized-Plan duplicate, and the union of 35 temporary-state candidates.
Overlapping reasons mean these category counts must not be added. No case was
removed merely to reach a target dataset size. The deterministic builder is
`scripts/tools/build_verified_playbook_clean_snapshot.py`.

The completed historical Offline GEPA minibatch-eight/eight-iteration run used
716 logical metric calls and six full validation evaluations. Its authoritative
audit interval was 2026-08-10 11:15:18 UTC through 23:50:08 UTC, approximately
12 hours 35 minutes. This is a planning reference, not a runtime guarantee for
the two-stage method. The no-repository Checker may be cheaper, while per-case
Reflectors and Curators add work; a separately authorized smoke must measure
the new balance before a formal 12--18 hour budget is claimed.

## Implementation Order

The agreed order is:

1. implement and contract-test the playbook representation, projection,
   per-rule Checker result, deterministic decision/score, two-stage Reflection,
   length gate, semantic Refiner boundary, and deterministic pruning;
2. audit and freeze the new SWE-bench Verified dataset and split;
3. design and freeze the Checker, Reflector, Curator, and length-Refiner prompts;
4. run no-LLM contract tests and a separately authorized bounded smoke;
5. freeze a formal experiment identity, fingerprints, budget, stopping rule,
   success criteria, and incomplete policy before any formal launch.

No existing frozen dataset, candidate tree, guideline, or result is modified
by this redesign.

## Prompt And Smoke Preparation

The first ACE-inspired prompt bundle was
`configs/prompts/offline_gepa_reject_playbook_v1_20260909.yaml`. It defines a
per-rule no-repository Checker, configurable-round per-case Reflector,
delta-only Curator, and length-only Refiner. Checker information isolation is
an adapter contract rather than a prompt-only instruction.

The completed first smoke contract is
`configs/gepa_verified_reject_playbook_smoke_v1_20260909.yaml`. It freezes four
train cases, two validation cases, one candidate proposal, at most eight
metric calls, a one-bullet placeholder seed, prompt and seed fingerprints,
success criteria, and the operational-incomplete policy. Reflection begins at
one round but remains configuration-controlled for later smoke comparison.

That smoke validated distributed submission and information isolation but
exposed two contract defects: ambiguous `plan_evidence` typing made valid
Checker reasoning score as malformed output, and inline historical trajectories
overflowed one Reflector context. It produced no candidate and is diagnostic,
not a successful method smoke. Its prompt, config, run, and fingerprints remain
unchanged for provenance.

The repaired, launch-unauthorized successor is
`configs/gepa_verified_reject_playbook_smoke_v2_20260910.yaml`, using
`configs/prompts/offline_gepa_reject_playbook_v2_20260910.yaml`. It makes
`plan_evidence` an explicit string array, checkpoints raw Agent completion
before Host validation, retries only the invalid array element with validator
feedback, restores file-backed Reflection evidence, and treats exhausted
proposal work as operationally incomplete.

The smoke implementation uses the established
distributed GEPA execution contract rather than calling Agents inside the
controller. The local supervisor submits short controller allocations. A
controller advances GEPA only until an Agent wave is needed, submits one
fingerprinted Slurm array, persists `task_state.json`, raises
`ControllerYield`, and immediately releases its allocation. A later controller
replays the same GEPA call and consumes valid atomic outputs.

The task granularity is:

- one Checker array element per case and candidate evaluation;
- one Reflector array element per selected minibatch case and reflection round;
- one Curator array element per proposal;
- one Refiner array element only when the visible candidate exceeds 10,000
  tokens;
- counter updates, OR aggregation, operation application, and final pruning
  remain deterministic host operations.

Every wave is fingerprint-bound. Completed validated task outputs are reused;
only missing, operationally failed, or Agent-output-contract-failed indices are
retried. Every attempt retains its raw Agent completion or trajectory,
validation failure, and Slurm status. Checker prompt values contain only issue,
Plan, the temporary Checker-visible playbook, and Host validator feedback on a
fresh retry; labels and historical evidence are joined only after collection.
Reflector manifests point to the retrospective evidence permitted by the
method instead of embedding it in the model request.

### Reused execution authorities

The redesign adds experiment-specific manifests and workers but reuses the
existing control plane:

- `src/optimization/hpc/task_batch.py`: arrays, atomic task state, attempts,
  selective retry, submission reconciliation, and controller yield;
- `src/optimization/hpc/slurm.py`: `sbatch`, `squeue`, and `sacct` transport;
- `src/optimization/resume.py`: reproducible sampler/selector state;
- `src/optimization/callbacks.py`: GEPA progress and checkpoint alignment;
- third-party GEPA's `GEPAState`: durable candidate/search checkpoint;
- `scripts/hpc_submit_batch.sh`: persistent remote run directory and short
  controller submission;
- `scripts/hpc_resume_loop.py` and `scripts/hpc_supervisor_service.py`: local
  supervision, active-worker awareness, and controller resubmission.

The earlier controller-direct `DirectPlaybookAgents` and its custom
`model_call_cache` were removed. No-repository describes the Checker evidence
boundary; it does not disable Slurm Agent workers or distributed execution.

The smoke uses `1 CPU / 4G` for each Agent array element, up to eight concurrent
elements, a 35-minute Agent limit, two fresh-Agent attempts, and a
`1 CPU / 4G / 30-minute` controller ceiling. The supervisor polls every 30
seconds; controller jobs normally terminate immediately after durable Agent
submission instead of holding the full allocation.
