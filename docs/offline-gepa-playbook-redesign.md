# Offline GEPA Reject-Playbook Redesign

> Authority: Offline GEPA reject-playbook method, development provenance, and
> reusable execution semantics
>
> Scope: the Offline GEPA method replacing the prior single-decision
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
| ACCEPT | 1 | 0 |
| REJECT | -1 | 1 |
| structurally INVALID candidate | -100 | -100 |

Relative to the correct decision, an unsupported rejection loses two points
and a missed rejection loses one. This encodes the deployment preference
against unfriendly repeated rejection while preserving a positive reward for
both correct cells. The scoring preference belongs to deterministic Host
configuration; it is not stated to the Checker, Reflector, or Curator.
Operationally incomplete or authority-uncertain cases must not be silently
converted to `UNRESOLVED`.

`INVALID` is reserved for a candidate playbook that violates the frozen
playbook contract, including a bullet longer than 64 Checker-model tokens. It does not mean
that a Checker returned malformed JSON. Checker output-contract failures retain
the raw Agent completion, receive Host validation feedback, and retry as fresh
Agent attempts; exhaustion is operationally incomplete and is not scored.
An overlength candidate receives `-100` per evaluated case without invoking
the Checker.
Curator length validation happens after its raw Agent completion is durable.
An overlength ADD, REVISE, or MERGE receives concrete Host feedback and is
retried with a fresh Curator, up to three attempts. If all three structurally
valid proposals remain overlength, the last proposal becomes an `INVALID`
candidate scored at `-100`; this specific exhaustion is not operationally
incomplete.
The global 2,048-token trigger is counted with the configured Checker model's
tokenizer, not whitespace-delimited words; the same count is supplied to the
Refiner and used by deterministic pruning.

The configured perfect score is one. `skip_perfect_score` skips a proposal only
when every case in the sampled minibatch is already perfect; it does not remove
individual perfect cases from a mixed Reflection minibatch. Score-table wiring,
reporting, and GEPA skip-perfect behavior require project-side contract tests.
Accuracy, balanced
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

Counter arithmetic is host-owned and global to one run rather than local to a
GEPA candidate branch. The host records immutable attribution by normalized
bullet text and instance ID, so revisiting the same case does not manufacture a
second vote. Before reflection, a parent candidate is hydrated from this
run-level ledger; after a complete valid proposal, new helpful/harmful evidence
is atomically persisted under the run directory. Failed Agent attempts and
invalid Curator or Refiner output do not update the ledger. The Checker never
receives this evidence.

A retained ID must preserve its complete bullet record. An exactly matching
rule on another branch shares the global evidence for that text. A
substantively revised or merged rule receives a new ID and new semantic text,
starts with zero counters, and records input IDs in lineage. The Curator cannot
assign or rewrite evidence counters.

The Curator must not expose IDs or counters in Checker-visible bullet text.
Rule-ID and counter inheritance for substantive revisions and merges must be
fixed before implementation so semantically different rules do not inherit
misleading evidence.

## Length Management

Length is measured deterministically on the Checker-visible projection. The
maximum is **2,048 tokens** under a tokenizer and version that must be frozen
in the implementation contract.

After Curator output:

1. If the rendered playbook is at most 2,048 tokens, no length-management
   Agent runs.
2. If it exceeds 2,048 tokens, a semantic Refiner removes duplication,
   compresses wording, and merges genuinely overlapping bullets. It must not
   add new rejection knowledge or inspect case trajectories.
3. The host renders and counts again.
4. If the result remains over the limit, the host deterministically removes
   whole bullets according to a frozen counter-based ranking until the limit is
   met. An LLM does not choose pruning targets and bullet text is never
   truncated.

The current pruning utility is `helpful - 2 * harmful`, matching the two-point
loss from a false rejection relative to a correct acceptance. The lowest value
is removed first. Ties remove higher-harmful, then lower-helpful, then longer,
then lexicographically earlier IDs. New bullets start at zero. Retained IDs
preserve their counters; merged bullets start at zero and record their source
IDs in lineage. The tokenizer and any protection period for new untested rules
remain to be frozen with the formal configuration. Every refinement, merge,
and deterministic removal must retain an audit record.

## Current Safe PCE Learning Authority

The current ACE input is derived from the new Safe PCE run rather than the old
482-case PCE snapshot. The raw run is never rewritten. Its exhaustive observed-
state cleaning ledger partitions the selected 482 cases into 411 reliable
terminal training cases, 10 leakage/evaluator exclusions, and 61 unfinished
cases deferred for later ACE evaluation. The deferred set is an operational-
leftover set, not a random or prevalence-representative holdout.

The compact learning snapshot is
`configs/frozen_swe_verified_playbook_gepa/20260915_safe_pce_clean411_v2/`.
It contains 411 cases (335 resolved, 76 unresolved) split deterministically into
329 train cases (266/63) and 82 internal-validation cases (69/13), stratified by
repository and outcome. These are development splits; the later 61-case set is
kept outside the snapshot.

Checker records retain repository identity only for dataset joins and audit.
The runtime Checker projection contains only issue, Plan, and visible playbook
text. Historical Plan/Code trajectories, patch, and evaluator result are stored
as path/hash/field references to the immutable raw output. They are hash-
verified and materialized only when a selected case's repository-free
Reflection evidence directory is written. The Curator likewise reads its 32
final case reflections from a read-only file bundle rather than from one large
inline prompt.

The replacement one-proposal smoke uses a frozen outcome-balanced 32-train/8-
validation selection, three sequential Reflection rounds, the explicit
`1/0/-1/1` score table, a 64-token bullet cap, and the 2,048-token playbook cap.
Its runtime and supervisor configs are
`configs/gepa_verified_reject_playbook_safe_pce_smoke32_v2_20260915.yaml` and
`configs/gepa_verified_reject_playbook_safe_pce_smoke32_v2_supervisor_20260915.yaml`.
The v1 authority is retained as a failed operational smoke: both Checker waves
completed, but the Reflection cache path kept `${USER}` literal and all 32
Reflector tasks exhausted before model execution. Runtime evidence paths now
expand environment variables and user-home markers before constructing the
isolated Apptainer environment.

The formal development membership is frozen separately in
`configs/frozen_swe_verified_playbook_gepa/20260915_safe_pce_clean411_v2/formal400-v1.json`.
It deterministically samples within the existing split and repository/outcome
strata: 320 train cases (258 resolved, 62 unresolved) and 80 validation cases
(67 resolved, 13 unresolved), for 400 total (325/75). Eleven clean411 cases are
outside the formal membership; their identities and strata remain in the same
selection authority. Outcome is used only to preserve development-set
composition, so this is not held-out evaluation evidence.

## Historical Pre-Safe-PCE Dataset Cleaning

Before Safe PCE, development used immutable derivatives of the historical
482-case SWE-bench Verified snapshot. The following material is retained only
to explain those earlier run identities and exclusions.

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

A second immutable eligibility pass audited final Plan length and structural
completeness. It excludes five trivial one-line artifacts and three
human-confirmed truncated or structurally incomplete artifacts. These are
recorded separately as `TRIVIAL_PLACEHOLDER_PLAN` and
`TRUNCATED_OR_STRUCTURALLY_INCOMPLETE_PLAN`; the latter includes one historically
resolved case, demonstrating why resolved outcome alone is not a Plan-artifact
quality check. The resulting authority is
`configs/frozen_swe_verified_playbook_gepa/20260910_clean436_4c0e9c40440e/`.

It contains 436 retained cases: 349 train and 87 validation, with 292 resolved
and 144 unresolved. Its v2 ledger remains exhaustive over all 482 source rows;
46 rows are excluded in total. The older clean444 snapshot remains frozen for
reproduction of the completed smoke and is not modified.

A third immutable pass applies only the agreed `ABRUPT_ENDING_PLAN` exclusion:
after trimming whitespace and trailing Markdown emphasis/backtick markers, a
Plan ending in an introductory colon is treated as a truncated artifact. This
catches endings such as `Run:`, `Current code:**`, and `Before:` without using
absence of a Validation section as an exclusion. The v3 authority is
`configs/frozen_swe_verified_playbook_gepa/20260910_clean375_127b1627efee/`.
It retains 375 cases: 297 train and 78 validation, with 259 resolved and 116
unresolved. Sixty-six source rows carry the abrupt-ending reason, five overlap
earlier exclusions, so this pass removes 61 additional clean436 rows. Seven
retained cases still lack a recognized Validation heading; they remain useful
potential Plan-deficiency evidence and are not removed by this policy.

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

## Development Provenance And Method-Changing Findings

This section retains terminal development evidence only when it changed the
method or is required to reproduce it. Live iteration counts, queue state,
candidate rankings, and ETAs remain runtime-artifact concerns as defined in
`documentation-authority.md`.

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

The repaired successor was
`configs/gepa_verified_reject_playbook_smoke_v2_20260910.yaml`, using
`configs/prompts/offline_gepa_reject_playbook_v2_20260910.yaml`. It makes
`plan_evidence` an explicit string array, checkpoints raw Agent completion
before Host validation, retries only the invalid array element with validator
feedback, restores file-backed Reflection evidence, and treats exhausted
proposal work as operationally incomplete.

The v2 smoke completed its Checker work but exhausted both Reflector attempts.
The saved JSON files were generally valid; the terminal submission was polluted
by an Apptainer working-directory warning because the environment combined
stdout and stderr. The v3 prompt/runtime repair therefore treats
`/tmp/reflection.json` as a separate Agent artifact authority, reads only its
stdout, retains stderr as diagnostics, supplies a complete positive JSON
template, requires an explicit JSON self-check, and gives Reflector retries the
prior Host validation error. The v3 prompt authority is
`configs/prompts/offline_gepa_reject_playbook_v3_20260910.yaml`; no v3 smoke is
authorized merely by this documentation.

The three-round bounded smoke used
`configs/gepa_verified_reject_playbook_reflect3_smoke_v1_20260910.yaml`. It uses
the clean436 authority, two train cases, one validation case, a one-case
Reflection minibatch, three sequential Reflection rounds, one proposal, and at
most five metric calls. `skip_perfect_score: true` makes the smoke exercise the
nonzero-loss case instead of spending its only Reflection slot reinforcing an
already correct classification. Expected active runtime is approximately 20
minutes excluding Slurm queue delay; the 35-minute per-Agent ceiling is a
failure bound, not an expected duration. The paired supervisor config is
`configs/archive/supervisor_launches/gepa_verified_reject_playbook_reflect3_smoke_supervisor_v1_20260910.yaml`.
The smoke completed on the frozen clean436 input at commit `c492ae2`. All three
sequential Reflector waves, both Checker waves, and the Curator completed on
their first task attempt with no operationally incomplete result. The three
attributions for `astropy__astropy-13033` evolved from `harmful` to `neutral`
to `neutral`; the Curator returned an empty delta, so no candidate beyond the
seed was evaluated. The active work took approximately 12 minutes and validates
three-round transport/resume behavior, not candidate-generation effectiveness.

The first reflection labeled the sole non-triggering placeholder rule harmful
because the complete rule set accepted a Bad case. That conflated an individual
rule's faithful abstention with missing rule coverage. Given the previous
reflection as evidence, round 2 corrected the attribution to neutral and
separated the concrete-but-wrong Plan from the anti-placeholder rule. Round 3
kept the same attribution and mainly increased confidence and explanatory
stability; it added no new actionable distinction. The formal design nonetheless
freezes three rounds as a conservative attribution-stability pass, by explicit
method decision rather than because the smoke demonstrated additional third-round
information gain.

The formal development contract was
`configs/gepa_verified_reject_playbook_formal_12it_v1_20260910.yaml`, paired with
`configs/archive/supervisor_launches/gepa_verified_reject_playbook_formal_12it_supervisor_v1_20260910.yaml`.
It consumes the complete immutable clean375 train/validation splits (297/78),
uses an eight-case Reflection minibatch, three sequential Reflection rounds per
case, twelve candidate proposals, and a 1,200 metric-call fail-safe. The frozen
expected elapsed budget is 12--18 active hours excluding Slurm queue delay. Each
Agent array element remains `1 CPU / 4G / 35 minutes`, with at most eight running
elements. Launch authority remained a separate explicit user decision.

The run subsequently reached six durable iterations in approximately one
active hour, then stopped during the next Controller submission. This was a
staging-quota failure, not a GEPA or Agent failure: contemporary
`ulhpc-submit` created one complete run-scoped workdir per short Controller
slice, and 37 copies occupied approximately 26 GB. The earlier Behavioral
eight-iteration run used the same mechanism, but its 34 smaller copies occupied
only approximately 9.4 GB; the still-earlier Offline workflow reused a fixed
remote tree.

The repaired submit wrapper restores that fixed-tree property without changing
the distributed experiment. At the supervisor's existing quiescent submission
boundary it synchronizes code into the dedicated `--remote-dir`, excluding
`output`, `.ulhpc_submit`, and the complete frozen dataset family staged
separately. `ulhpc-submit --no-sync` then submits from that fixed tree. No next
sync occurs while a Controller or Agent worker is active. Persistent run-state,
dataset staging, task attempts, Agent artifacts, and checkpoints are unchanged
and are never cleanup targets.

The authorized continuation is
`configs/gepa_verified_reject_playbook_formal_30it_resume_v1_20260910.yaml`,
paired with its `formal_30it_resume` supervisor config. It retains the same
logical run and changes cumulative iterations from 12 to 30 and the metric-call
fail-safe from 1,200 to 3,000. Before resume,
`scripts/internal/extend_playbook_budget.py` verifies that predecessor and
successor configs differ only in whitelisted budget/documentation fields,
checks the predecessor fingerprint, records the transition, and atomically
updates the manifest fingerprint. It is idempotent and refuses changes to data,
split, prompts, models, scoring, sampling, Reflection, or run paths.

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
- one Refiner array element only when the visible candidate exceeds 2,048
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
elements, a 35-minute Agent limit, three fresh-Agent attempts, and a
`1 CPU / 4G / 30-minute` controller ceiling. The supervisor polls every 30
seconds; controller jobs normally terminate immediately after durable Agent
submission instead of holding the full allocation.

### Candidate 1 Curator replay

`configs/gepa_verified_candidate1_curator_replay_v1_20260910.yaml` reuses the
fingerprinted Curator task immediately preceding Candidate 1: the counted seed
playbook plus all eight final-round case reflections. It submits only the
Curator singleton. Checker, Reflector, Code Execution, and GEPA search are not
rerun. Each prompt revision should use a new replay run identity and prompt
fingerprint while retaining the frozen source-task fingerprint.

The replay confirmed that the atomicity and readability constraints can turn
eight case reflections into a small set of short, independent bullets without
mechanical case-to-bullet mapping. It also clarified the intended evidence
boundary: downstream evidence may reveal a Plan deficiency when the relevant
condition already existed and could reasonably have been discovered before
implementation through planning, software-engineering reasoning, or repository
exploration. The historical Planner need not actually have found it. A bullet
must not instead require a historical Code action, test outcome, evaluator
result, runtime failure, or environment incident as its trigger.

Fresh runs use the atomic seed
`configs/development_guidelines/offline_gepa_reject_playbook_seed_v2.json`,
whose sole rule is `The Plan is a placeholder.` The earlier v1 seed remains
unchanged because it is fingerprinted by completed and resumable experiments.
The paired prompt authority is
`configs/prompts/offline_gepa_reject_playbook_v5_20260910.yaml`. The fresh
30-proposal contract is
`configs/gepa_verified_reject_playbook_formal_30it_v2_20260910.yaml`; it starts
a new candidate tree rather than importing or resuming earlier candidates.

That v2 contract exposed a control-plane regression: its tool-using Reflector
retained an internal 20-step limit and a 120-second command timeout. Three early
Reflector tasks reached `LimitsExceeded`, causing otherwise avoidable retry
waves. The replacement v3 contract removes the internal step limit, restores
the 1800-second per-command ceiling, and leaves the complete Agent-session
ceiling to the 35-minute Slurm task wall time. Because runtime source and
execution semantics changed, v3 starts a fresh candidate tree and does not
resume v2 state.

The v3 run subsequently completed all 30 proposal iterations with 882 logical
metric calls and produced six accepted candidates in addition to the Seed.
On its 78-case development validation set (54 resolved, 24 unresolved), the
asymmetric scalar objective retained candidate 0, the one-rule Seed, as best:
its aggregate score was -0.3718. The six learned candidates scored from
-0.6026 to -1.5897. Learned candidates increased bad-plan recall from 0 to a
maximum of 0.625, but also increased costly false rejection from one case to
between five and 23 cases; the recall gain therefore did not improve the
declared scalar objective. These are resolved-proxy classification results,
not PCCE or intervention-benefit evidence.

A compact local authority is frozen at
`configs/frozen_guidelines/ace-formal-v3-all-candidates-v1-20260911/`. It
contains the semantically exact Seed and six playbooks, the exact run manifest,
candidate lineage, and reconstructed validation metrics. The large raw GEPA
result and Agent evidence remain under the run's Iris authority.
