# Offline GEPA Reject-Playbook Redesign

> Authority: Offline GEPA reject-playbook method, development provenance, and
> reusable execution semantics
>
> Scope: the Offline GEPA method replacing the prior single-decision
> guideline proposal while retaining third-party GEPA search where practical

## Objective And Boundary

### Paired Level formal 8-iteration preparation (2026-09-21)

The original authority is
`configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_20260921.yaml`
with its matching supervisor YAML and versioned `v2` prompt bundle. It starts
from the categorized placeholder seed, uses 143 explicitly selected train
pairs and the unchanged 36-pair validation split, 24 pairs per reflection
minibatch, eight proposals, and a 1200 pair-metric-call ceiling. The one
excluded Django pair is documented in
`docs/knowledge/paired-gepa-django-11749-exclusion.md`; the frozen source
snapshot and completed smoke are unchanged. The new Checker instruction
forbids prototyping a proposed change in repository files, temporary copies,
or memory, without prompting it to run probes. This is a prompt instruction,
not a new tool-level command filter.

The original-run lineage recovery is described in
[`paired-levels-v1-resume.md`](paired-levels-v1-resume.md). It retains v1's
candidate pool and first six iterations, replays the pending seventh draw and
completed Agent evidence, then performs the normal eighth iteration. The v2
Seed-based reconstruction is aborted provenance, not a continuation of v1.

The Iris transport request remains 1 CPU / 4G for both Controller and Agents,
with 35-minute Agent limits and three attempts. Supervisor and Controller reuse
the existing Slurm array, checkpoint, retry, and conservative cleanup paths.
Before launch, verify remote SIF/history artifacts, scratch byte and inode
capacity, clean worktree, and absence of a duplicate supervisor. Preparation
and commit do not authorize launch.

### Lightweight paired smoke contract (2026-09-21)

Runtime: `configs/gepa_verified_paired_levels_smoke8_v1_20260921.yaml`.
Launcher: `configs/gepa_verified_paired_levels_smoke8_v1_supervisor_20260921.yaml`.
Selection: `configs/frozen_swe_verified_plan_pairs/20260921_paired_levels_smoke8_v1/selection.json`.
This separately authorized preparation does not launch jobs or authorize 8it.
The prompt bundle retains its draft filename but its exact reviewed bytes are
hash-bound by this smoke config; do not edit them after launch.

| Item | Frozen smoke choice |
|---|---|
| Universe | Existing frozen 144/36 paired development split; no new split |
| Sampling | First serialized pair per Astropy, Django, Matplotlib, SymPy in each split; 8 distinct tasks |
| Train / validation | 4 / 4 pairs, task-disjoint; development diagnostic, not held-out evidence |
| Search | One proposal, minibatch 4, one Reflector per pair, 32 pair metric-call ceiling |
| Checker | Explicit thinking disabled; read-only repo; 0/1/2 results; only 2 gates |
| Other models | Unchanged provider-default reasoning; no new explicit high setting |
| Resources | Iris Controller and Agents 1 CPU / 4G; Agent limit 35min; 3 attempts |
| Supervisor | Existing shared loop, 30s polls, 30min Controller slices, at most 6 submissions |
| Storage | Canonical shared paths; reclaim inactive staging/workspaces; retain scientific evidence |

Reuse prepared base-ancestor history bundles, Slurm arrays, raw-completion
checkpoints, and Host validation/retries. No historical binary outputs are
imported. Before launch check cluster capacity/quota, credentials, SIFs and
history availability; preparation does not claim a current remote preflight.
Six Controller submissions bound resumption, not elapsed wall time including
queue waits. Agent jobs release resources when done rather than occupying the
whole requested 35 minutes.

Success means the Seed evaluation, paired Reflection, Curator proposal path,
and any resulting candidate evaluation finish with valid artifacts. Empty
operations or no accepted candidate are permitted; record branches that were
not exercised. Check Level calibration, exploration depth, category readability,
counter continuity and retry behavior. Separate queue time from actual Agent
time when assessing speed. This small smoke does not validate minibatch-24
context capacity or demonstrate learning gains. Operational failures remain
incomplete, never R/U or candidate INVALID. Stop after the single proposal or
the metric ceiling; exhausted operational failures stop and remain reportable.

### Categorized paired-Level development (2026-09-21)

The opt-in development config is
`configs/gepa_verified_paired_levels_categorized_dev_v1_20260921.yaml`.
It is deliberately `status: draft`, rejected by the execution entry point,
and has no launch configuration. Existing no-repository, unpaired Repo-Level,
and binary paired contracts remain supported; their frozen files are unchanged.

Mechanisms implemented for the next design review:

- `repo_checker.output_contract: levels_v1` replaces the paired Agent's
  `triggered` field with integer `level`: 0 (no supported concern), 1 (warning),
  2 (pause). Only Level 2 rejects. Levels 1/2 require finding and evidence.
  The existing +1/-1/0 pair scoring and overlength INVALID=-100 are unchanged.
- Both side findings and Levels remain in the Reflection evidence, including
  Level 1 warnings. Level is not stored in permanent playbook bullets and is
  not mapped automatically to helpful/harmful counters.
- Checker explicitly passes `thinking: disabled` to the existing provider
  adapter. Reflector/Curator/Refiner omit that field and retain the previous
  provider-default behavior; this is not a claim that the old requests
  explicitly selected high reasoning.
- `repo_checker.review_only: true` makes only the prepared Checker repository
  mount read-only after Host baseline restoration. Phase-local `/tmp` remains
  writable for submission. Reflector and other tasks are unaffected. This
  does not prohibit running tests or making scratch copies: investigation
  purpose and depth still require the pending behavior-prompt review.
- Optional per-bullet `category` is a single-line display heading. Renderers
  preserve bullet order/ordinals, repeating a heading when needed rather than
  silently regrouping bullets. Titles have no gate, result, or counters and
  count toward the total visible-token budget. IDs/counters remain hidden.
- Categorized Curator ADD/REVISE/MERGE operations include category. Refiner
  preserves categories and cannot merge across them. Existing ID, lineage,
  counter, and deterministic length-pruning semantics remain unchanged.
- The new seed is `development_guidelines/paired_concern_seed_v3_categorized.json`:
  category `Plan substance`, text `The Plan is a placeholder.`, initial ID
  `plan-00001`, helpful=0, harmful=0. Older seeds remain frozen.
  Categorized playbooks render with `Reject the plan when:` and `Rule 1.`
  ordinals, not the historical Repo Checker's `Concern 1.` presentation.

The new prompt bundle is a **behavior-review draft**, not launch-ready. Checker
is asked for quick targeted review, not trial implementation or test execution.
Reflector reports Level calibration and whether rule wording or Checker
application explains a finding, using existing pair_analysis/attribution fields.
Neutral tags may retain calibration evidence; empty concern lists may retain
uncertainty. Curator synthesizes this evidence into scoped rules and categories,
with no preferred operation type or quota. Templates no longer default to a
negative Checker decision, neutral reflection, or ADD operation. These changes
reduce identified prompt biases but their behavioral effect remains untested.
The existing Reflection schema, counter mechanism, and evidence scope are
unchanged; no cross-run supporting-task library is introduced. Review the prompts
and freeze smoke inputs/budget before launching. Refiner's existing same-category
merge constraint remains a separate design choice, not relaxed by prompt alone.

The remainder documents retained execution variants. The old binary paired
protocol is not the new `levels_v1` contract.

The method remains a classification experiment. It does not add Plan revision,
Code generation, or a new evaluator execution to candidate scoring. It now has
two explicitly separate Checker modes over the same internal playbook:

- the retained no-repository mode, which sees only issue, Plan, and rule text;
- the additive repository-aware concern mode, which also inspects a disposable
  frozen base-commit checkout and assigns a case-level concern Level.

The no-repository mode and all of its frozen prompts, configs, candidates, and
results remain unchanged. The new mode is a separate experiment identity, not
a silent semantic change to those runs.

The target artifact remains a concise, human-readable list of Plan concerns.
The retained projection is:

```text
Reject the plan when:
- ...
- ...
```

The repository-aware projection uses the heading
`Review the Plan for these concerns:`. Bullets contain reusable concern
knowledge but no stored severity. General software-engineering investigation
remains a Checker capability.

Both Checkers may see the issue, proposed Plan, and rendered bullet text. Only
the additive Repo Checker may inspect the frozen base repository. Historical
labels, implementation trajectories, patches, evaluator results, rule
identities, counters, and Reflection analyses remain outside both Checker
boundaries.

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

Rule 1. The Plan is a placeholder.

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

## Additive Repository-Aware Concern Mode

The Repo Checker evaluates every visible concern independently and returns a
case-specific Level. The intended Level interpretation for the next paired
protocol is about the likely need for intervention, not a permanent property
of a playbook bullet:

| Level | Operational meaning | Host gate |
|---|---|---:|
| 0 | No material concern, or a supported minor gap that ordinary implementation can readily repair. | no |
| 1 | A supported concern whose repair may require investigation or re-planning; report it, but do not pause automatically. | no |
| 2 | A supported concern unlikely to be repaired reliably without pre-implementation clarification or revision. | **yes** |

The Host derives `REJECT` only when at least one triggered concern is Level 2.
Level 0 and Level 1 are retained as richer diagnostic output and later
Replanner input, but do not directly affect current candidate selection. The
Checker, rather than the Host, assigns Level; the Host only validates the
schema and applies the deterministic Level-2 gate.

Playbook bullets never store a default Level. The same concern may be Level 0,
1, or 2 on different cases according to issue, Plan, and repository evidence.
The completed 2026-09-21 eight-iteration run used narrower prompt wording:
Level 0 meant no finding, Level 1 a non-pausing warning, and Level 2 a pause.
Its Checker behavior must be analyzed under that **historical** prompt, not
retroactively relabeled with the intended repairability meanings above. Host
validation now permits an evidenced Level-0 minor finding for the next prompt
revision; the old prompt still asks for null/empty Level-0 fields. Candidate
selection still measures Level-2 pair ordering, not Level-1 calibration.

The current paired Reflector sees the same frozen base repository plus
retrospective Plan/Code/evaluator evidence. It emits free-text `pair_analysis`,
zero or more `reusable_concerns`, `uncertainty`, and per-bullet helpful/neutral/
harmful tags. It does **not** emit a structured Coder-recovery class or separate
per-side concern inventory. Helpful/harmful tags update global counters; they
are not Level labels. Consequently, a shared Plan gap repaired by only one
Coder can be described in free text yet fail to reach Curator as a reusable
concern. This is a known learning-information gap, not a reason to discard all
Coder-divergent pairs. A future Reflector/Curator prompt and contract revision
must explicitly resolve it before another learning run.
The paired review validator no longer requires or forwards a separate
`confidence` field. It accepts and strips that field from historical paired
prompt outputs for replay compatibility. Confidence in older no-repository,
repository-binary, and Behavioral protocols is separate historical authority,
not a field in the next paired learning contract. The frozen 2026-09-21 paired
prompt still requests confidence and must be superseded before a new run.

Each repository Checker or Reflector is still one Slurm array element. It uses
the frozen audited SIF, copies `/testbed` into a phase-local disposable
worktree, restores the declared base commit with future history pruned,
isolates home and `/tmp`, masks the image package cache, suppresses the host
working-directory bind, and submits JSON through a separate artifact file. It
also reuses the Safe PCE `conservative_blacklist_v3` source-access boundary:
literal task URLs are extracted by the Host, relevant Git/pip/HTTP commands are
classified before execution, and events are retained in a per-attempt audit
log. This is enforced by the runtime rather than described as Checker review
knowledge. Outcome, Code trajectory, evaluator result, stable bullet IDs, and
counters are absent from the Repo Checker task manifest. Malformed output is a
retryable Agent contract failure; it is not candidate `INVALID`.

The first development smoke used an outcome-balanced 8-train/4-validation
subset of already exposed Safe-PCE development cases. Its twelve Seed Checker
calls completed and returned ACCEPT, but every Repo Reflector attempt failed
before model execution: the proposal layer supplied `source_access_issue`, while
the Slurm manifest projection omitted it and the Worker raised `KeyError`.
Consequently v1 produced no Reflection, Curator call, or candidate and is only
operational failure provenance; it says nothing about the new prompts or Level
calibration.

The repaired successor is
`configs/gepa_verified_repo_concern_playbook_smoke8_v2_20260915.yaml`. The
executor now requires and preserves the source-policy input in the final Repo
Reflector task manifest while keeping it outside `prompt_values`. A regression
test inspects that final persisted manifest, rather than only the proposal
layer's intermediate item. V2 retains the same cases, prompts, one-proposal
budget, and one Reflection round for a controlled retry. It imports the twelve
completed v1 Seed Checker outputs only after exact scientific-input matching,
source run-manifest hash verification, source-output identity checks, and the
current Host Checker validator. The original `agent_output` and trajectory are
preserved; the v2 envelope records source task/output paths and hashes. Inputs
for a learned candidate differ and therefore cannot match these Seed
checkpoints. Its first v2 launch failed before any Agent call because the
relative source-run path resolved within the submission worktree rather than
the canonical run-state root. That authority remains unchanged. The v3
successor binds the same source run through its absolute canonical scratch path
and retains the same source manifest hash and all other method inputs.

V3 successfully imported all twelve Seed Checker results, completed eight Repo
Reflectors and one Curator, and produced a ten-concern candidate. Its Candidate
Checker then exposed a Prompt/Host contract defect: the prompt showed only an
empty evidence array, while Host validation required every evidence object to
contain exactly `source`, `location`, and `observation`. Agent trajectories
explicitly show the model guessing a two-field schema; the generic retry error
did not reveal the missing field. All eight first attempts therefore failed
output validation even though their substantive reviews were present. V3 was
stopped and its remaining second-attempt array cancelled; these are operational
failures, not method scores.

The prepared v4 successor adds a positive, hash-bound exact-schema appendix
with both triggered and untriggered examples. Host validation errors now name
the exact required keys. V4 imports the completed v3 Seed Checker, Repo
Reflector, and Curator outputs only when task inputs match; for file-backed
roles, immutable evidence-tree hashes replace run-local paths in that match.
Imported outputs are revalidated and retain source manifest/task/output hashes.
The failed Candidate Checker outputs are not eligible for import, so the first
new Agent wave is the corrected Candidate Checker evaluation.

## Two-Stage Reflection

Reflection is separated into per-case attribution and cross-case curation.
Only cases selected for the GEPA Reflection minibatch receive a Reflector run;
ordinary full-validation evaluation does not create Reflector calls.

### Stage 1: Per-Case Reflector

The retained no-repository Reflector reads one case's repository-free evidence
bundle. The additive Repo Reflector receives the same bundle plus a separately
isolated frozen base repository:

- issue and Plan;
- current internal playbook and Checker-visible projection;
- complete Checker trajectory and per-rule trigger results;
- host-derived Plan decision and score;
- frozen `RESOLVED` / `UNRESOLVED` proxy label;
- historical Plan and Code trajectories, patch, evaluator result, and outcome
  authority metadata available in the cleaned Reflection bundle.

The retained Reflector produces its frozen structured case analysis. The Repo
Reflector instead produces nullable `case_analysis` and `uncertainty`, zero or
more atomic `reusable_concerns`, and one tag for every active bullet ID. The
permitted tags are `helpful`, `neutral`, and `harmful`. Their exact evidence requirements
are frozen in the selected mode's Reflector prompt and output contract.

The Reflector uses the trajectory and result to make the attribution; it does
not infer tags from the confusion cell alone. In particular, a trigger on an
`UNRESOLVED` case is not automatically helpful when its stated reason is
unsupported or unrelated to the observed implementation failure. Neutral tags
do not change counters.

The Reflector neither edits the playbook nor updates counters. Its structured
output and full trajectory are immutable proposal evidence.

Large trajectories are stored as separate files rather than interpolated into
one model request. The bundle is mounted read-only and the Reflector reads
manifest-listed files on demand. The retained mode uses an isolated generic
evidence environment with no SWE repository. The Repo mode uses an isolated
case SIF and a separate read-only evidence mount. In both modes, the Checker
receives neither the retrospective bundle nor any container/SIF information in
its model input.

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
state cleaning ledger initially partitioned the selected 482 cases into 411
provisionally retained terminal training cases, 10 leakage/evaluator
exclusions, and 61 unfinished cases deferred for later ACE evaluation. A later
ACE trajectory audit identified one further evaluator-invalid retained case,
as recorded below. The deferred set is an operational-
leftover set, not a random or prevalence-representative holdout.

The compact learning snapshot is
`configs/frozen_swe_verified_playbook_gepa/20260915_safe_pce_clean411_v2/`.
It freezes 411 cases (335 resolved, 76 unresolved) split deterministically into
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

That v2 smoke completed as a development diagnostic. Repeating the same
Reflector prompt three times did not provide staged roles: each later round
received only the prior reflection in addition to the same evidence and task.
In the earlier bounded comparison, round 2 corrected a `harmful` attribution
caused by an ambiguous neutral-tag instruction, while round 3 added confidence
but no actionable distinction. The v6 prompt already encodes the corrected
neutral-tag rule. In the completed 32-case v2 smoke, later rounds instead
increased compound insights and cleanly separated resolved cases with no
concern from unresolved cases with concerns, which is consistent with
outcome-conditioned rationalization rather than additional decision-time
information. The next development authority therefore uses one Reflection
round; this is a method choice for the current repeated-prompt design, not a
claim that multi-stage Reflection is never useful.

The corresponding v7 Reflector replaces the scalar `key_insight` with an
explicit `reusable_concerns` list. Each element contains one concern, its
decision-time issue/Plan support, and a confidence value; an empty list means
that no reusable concern is supported. Runtime validation accepts this schema
while retaining the legacy scalar schema solely to read frozen historical
artifacts. Curator evidence indexes preserve whichever frozen schema supplied
the review.

The v2 audit also found that `pylint-dev__pylint-4604` had been labeled
unresolved after the official evaluator collected zero tests because the
historical Pylint test suite failed during import. The frozen v2 selection and
run remain unchanged. The new selection authority replaces that case with
`sphinx-doc__sphinx-10614`, a same-label case with a completed evaluator and
concrete FAIL_TO_PASS failures. Its v3 config is prepared but not launched.
Because `formal400-v1.json` also contains `pylint-dev__pylint-4604`, it is not
a launch authority until a separately frozen reliability-replacement formal
selection supersedes it.

That successor is
`configs/frozen_swe_verified_playbook_gepa/20260915_safe_pce_formal400_v2/selection.json`.
It replaces the invalid train/unresolved Pylint case with
`django__django-12273`, the only previously unselected case in the same split
and outcome stratum. Its official evaluator completed normally: both target
tests failed, while 27 PASS_TO_PASS tests succeeded. The replacement therefore
preserves 320 train and 80 validation cases and the overall 325 resolved/75
unresolved composition without treating an evaluator collection failure as a
Bad implementation.

The prepared formal development run is
`configs/gepa_verified_reject_playbook_safe_pce_formal_8it_v1_20260915.yaml`.
It starts a fresh candidate tree from seed v2, uses prompt v7, eight candidate
proposals, a 32-case Reflection minibatch, one Reflection round per selected
case, the explicit `1/0/-1/1` resolved-proxy score, a 1,600 metric-call
fail-safe, and the existing 64-token bullet/2,048-token playbook limits. Its
Supervisor uses shared storage defaults, unconstrained Slurm array submission,
35-minute Agent elements, three task attempts, durable checkpoints, and
staging reclamation. Preparation and commit do not authorize launch.

The first authorized launch of that configuration submitted Controller job
`5990365`, which failed before any Agent or metric call. The fixed remote
worktree intentionally excluded the complete frozen dataset family and staged
the dataset snapshot separately, but the formal selection was a sibling frozen
authority and was therefore absent when Controller fingerprint validation
began. The shared submit wrapper now discovers `inputs.selection`: when it is
outside the dataset snapshot, its containing frozen directory is independently
staged and linked at the repository-relative path. A dry-run and contract test
verify both staging/link pairs. The replacement run and Supervisor use the v2
identity; v1 remains failure provenance and is not resumed.

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

### Historical payload externalization

The inline `cases.jsonl`, `train.jsonl`, and `validation.jsonl` payloads for the
clean444, clean436, and clean375 snapshots were removed from the unpublished
Git history on 2026-09-15. They duplicated large raw Planner, Coder, and
evaluator trajectories and are not inputs to the current Safe-PCE-derived ACE
method. Their original manifests, membership hashes, audit ledgers, exclusions,
and cleaning rationale remain tracked unchanged. Each snapshot now includes an
`external_payloads.json` authority with the exact payload SHA-256 values and the
Iris operational-copy location verified on the externalization date.

The Iris scratch copy is an operational reproduction source, not a claim of
permanent archival storage. Recover a historical payload only for an explicit
audit or rerun, verify it against both `external_payloads.json` and the original
manifest, and do not recommit it. The historical run configurations that name
these snapshots therefore require that explicit restore step. Compact frozen
candidate pools and the current reference-based Safe PCE datasets remain in
Git.

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

## Within-task paired concern learning

The current development method replaces cross-task resolved/unresolved
classification with a within-task comparison. One data point contains two
different Plans for the same issue and frozen base repository: one historical
attempt resolved the official task and the other did not. All pairs from one
task belong to exactly one split.

For each pair, the runtime submits two independent repository-aware Checker
Agents. Each sees only the issue, one Plan, the visible concern text, and the
isolated base repository. The Checker sees neither the pair identity or side,
nor either outcome, Code trajectory, patch, evaluator result, bullet ID, or
counter. The Host derives a binary decision for each side and scores the pair:

| Checker decisions | Pair score |
|---|---:|
| accept resolved Plan; reject unresolved Plan | +1 |
| reject resolved Plan; accept unresolved Plan | -1 |
| accept both or reject both | 0 |
| structurally valid candidate with a bullet over 64 tokens | -100 |

One repository-aware Reflector then receives both completed Checker results and
both hash-bound historical trajectories. It must separate Plan differences
from Code behavior, evaluator strictness, runtime incidents, and sampling
noise. It may return zero reusable concerns. The Curator receives all paired
reflections in the selected minibatch and may add, revise, delete, or
duplicate-only merge atomic high-level developer concerns. This paired mode is
additive: the previous no-repository and Level-based repository modes remain
unchanged.

The frozen development authority contains 1,112 additional terminal
observations over the clean411 baseline. Reliability cleaning excludes
successful non-Prompt solution-source HTTP access, two patch-authority
mismatches, and the full `psf__requests-2317` task whose identical patch
received conflicting official outcomes under observed environment noise.
Blocked access attempts remain auditable but are not treated as leakage. The
result is 180 distinct-Plan R-by-U pairs across 57 tasks: 144 pairs from 46
train tasks and 36 pairs from 11 validation tasks. The compact observation
index remains in durable Aion scratch and is bound by SHA-256; Git stores the
pair snapshot, source manifest, exclusions, and audit rather than the raw
trajectory payload.

Repository Agent source auditing starts after Host base-repository preparation
and history sanitization, before the Agent's first action. Host preparation has
its own `repository_baseline` evidence; its compound Git commands must not be
reported as Agent source-access attempts. The Agent network policy is unchanged.
Repo Checker and Repo Reflector now install verified Safe PCE history bundles
before restoration. The whole wave checks bundle availability and integrity
before submission; missing artifacts require preflight preparation. Agent jobs
never fall back to legacy aggressive Git garbage collection. See
[`engineering-contracts.md`](engineering-contracts.md) for the shared contract.

Paired Reflection retains file-backed evidence mounted read-only at `/evidence`.
The initial prompt points to the bundle; reading complete trajectories can still
expand the Agent's conversation context. Only `reflection.rounds` configures
paired Reflection; its repository environment and command timeout come from
`repo_checker`. Curator still uses a repository-free evidence container: its
default cache comes from `container.sif_cache_dir`, its default image is
`python:3.12-slim`, and its command timeout is 1800 seconds. Historical
`reflection.evidence_*` and `reflection.command_timeout_seconds` fields remain
supported overrides for evidence Agents; they were never universally unused.
Validate this configuration before starting Checker waves. With
`abort_on_operational_incomplete`, exhausted proposal failures stop search at
the next loop boundary and surface as controller failures rather than consuming
subsequent minibatches.

The initial binary paired contract had no Level field. The completed
2026-09-21 formal run instead used per-rule Levels, categories, eight proposals,
and a 24-pair Reflection minibatch. Its historical train selection contained
143 pairs after one Django test-patch-collision exclusion. A later audit found
two more pairs using the same invalid Django U observation and three pairs using
a Requests U observation whose target test hit an external HTTP 502. The
superseding, unlaunched selection in
`configs/frozen_swe_verified_plan_pairs/20260922_operationally_clean138_v1/`
removes those five pairs while retaining the unchanged frozen snapshot and
36-pair validation split. The latter has not received the same new audit.

The completed run's Curator did not reliably read the full detailed Reflection
file: one observed round read only the summary index; another printed only the
first 3,000 characters of the detailed file. The index contains all concern
summaries and tags but omits full `pair_analysis`, including Coder compensation
and Level-attribution detail. Prompt instruction alone did not guarantee full
evidence consumption. The Curator operation's historical `risk_analysis` field
was required by Host but discarded rather than used in the rule or metric; it
is no longer required by Host, with old outputs accepted only for replay.
The frozen prompt still requests that field and must not be reused as a new
method prompt.
The eighth-round Curator explicitly printed only the first 3,000 characters
of the detailed Reflection file. The seventh-round Curator read the complete
summary index but not the detailed file. Neither behavior was caused by normal
shell-output truncation: the Apptainer command runner captures full stdout,
and the observed trajectory shows the Agent's own limited-read commands.
Merely strengthening a read-all instruction does not guarantee that every pair
is considered.

### Paired-learning prompt diagnosis and next smoke

The completed 2026-09-22 learning12 replays established that explicit
per-finding coverage makes the Curator read all indexed evidence and that
per-side findings recover Coder-compensated concerns. They also showed that a
single field combining case mechanics with reusable knowledge encourages
case-shaped rules, and that asking the Reflector to recommend promotion gives
it an unintended knowledge-admission role.

The v5 development prompt therefore treated the Agent roles as a learning path,
not as a hand-written definition of important Plan defects. The Reflector
records per-Plan findings, separates repository-specific `case_mechanism` from
portable `developer_concern`, and describes `pair_relation`, `evidence_role`,
decision-time support, and observed Coder repairability. Those fields describe
what the pair establishes; none automatically determines ADD, DEFER, or a
future Level. The Curator reads the complete record, proposes atomic portable
rules, and dispositions every finding. Side findings may support, refine, or
disqualify a reusable concern, but cannot bypass the same decision-time,
abstraction, and attribution analysis. Visible rules remain Level-free. Its
completed two-phase smoke exposed a new method effect: requiring a disposition
for every reusable and side finding made both Curators mark every finding
`USED` (35/35 and 41/41). The four `curation_assessment` enums did not prevent
this behavior. The audit mechanism therefore changed the behavior it was
intended to observe and is not part of the next prompt contract.

The completed v5 development smoke used the same twelve audited train pairs
and four exposed operational validation pairs in two sequential phases. Phase one
imports only the frozen seed Checker outputs and replays Reflector plus Curator
under v5. Phase two starts from the manually audited ten-rule development
playbook and runs the complete Checker, Reflector, and Curator path, approximating
a later learning state. The shared supervisor executes phase two only after
phase one exits successfully. The two run configs and the sequence supervisor
are `configs/gepa_verified_paired_learning12_smoke_v3_ref_cur_20260923.yaml`,
`configs/gepa_verified_paired_learning12_smoke_v3_manual_full_20260923.yaml`,
and `configs/gepa_verified_paired_learning12_smoke_v3_sequence_supervisor_20260923.yaml`.
These configs are completed development provenance and are not relaunch
templates.

The superseding v6 prompt is
`configs/prompts/offline_gepa_paired_levels_ace_core_v6_20260923.yaml`. It
restores an ACE-style backbone: diagnose the paired attempts, distill reusable
developer concerns, then let the Curator integrate only durable missing
knowledge. Full `side_findings`, pair analysis, and tags remain persisted in
`case_reflections.json` as an audit/diagnostic side channel. They are not copied
into the Curator's required index. The required `reflection_index.json`
contains only reusable concerns and material uncertainty; the Curator may read
the complete record when a distilled item is unclear or conflicting. New v6
runs use `reflection.distilled_curation: true` and
`curation.evidence_contract: distilled_v1` with finding coverage disabled.
The prepared, unlaunched v6 smoke is a two-phase sequence. Phase one,
`configs/gepa_verified_paired_learning12_smoke_v4_ace_core_ref_cur_20260923.yaml`,
imports the frozen Seed Checker outputs and isolates the new Reflector/Curator
behavior. Phase two,
`configs/gepa_verified_paired_learning12_smoke_v4_ace_core_20260923.yaml`,
starts from the manually audited ten-rule input and tests the complete Checker,
Reflector, Curator, and candidate-evaluation path. Both use the same twelve
development pairs and four exposed validation pairs as v5. The sequence uses a
60-second Supervisor poll interval and a ten-minute Agent Slurm limit. In the
completed v5 sequence, the slowest comparable Agent used 200 seconds, so this
retains approximately three times the observed maximum. Each phase allows up
to twelve short Controller continuations; this does not enlarge the one-proposal
scientific budget. The v5 full-cycle phase needed nine continuations because
each Agent wave yields Controller state. Preparation does not authorize launch.

Historical paired configs and prompts are indexed under
`configs/archive/paired-levels-20260921/README.md` without moving their
fingerprinted files.
