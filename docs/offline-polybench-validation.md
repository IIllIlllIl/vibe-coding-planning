# Offline Guideline PolyBench Generalization Validation

> Authority: external validation contract and frozen first-stage outcome for
> the current standalone Offline guideline method
>
> Last reviewed: 2026-08-28

## Purpose And Non-Training Boundary

PolyBench is an external generalization evaluation. Its issues, plans, labels,
Checker predictions, trajectories, metrics, and error analysis must never enter
GEPA training, Reflection, candidate proposal, candidate acceptance, guideline
repair, prompt design, metric selection, or stopping decisions.

All guideline-producing experiments that will be compared, including the
completed minibatch-eight and 3x3 runs, must finish and freeze
their candidate identities before any new PolyBench Checker result is viewed.
They are then evaluated together. A later method change informed by PolyBench
would require a different untouched benchmark for a new final generalization
claim.

This is Checker-only evaluation after data preparation:

```text
frozen PolyBench issue + newly generated plan + exact base environment
  + frozen guideline
    -> current Checker -> predicted_resolved
    -> controller-only comparison with the new PCE resolved label
```

It does not call `gepa.optimize` or Reflection.

## Retired Historical Input

The historical 198-case PCT snapshot and its derived Raw-198/Cleaned-192
validation input are unsafe as formal generalization data and are retired. They
remain historical audit evidence only. In particular:

- the 199-to-198 selection inherited an old execution failure;
- Agent and evaluator runs preferred `:v1.1` but later allowed
  `v1.1 -> v1.0 -> latest -> local Dockerfile build`;
- the final image ref and OCI digest were not stored per Plan/Code/Evaluate
  phase;
- the Hugging Face dataset revision and exact PolyBench harness source were not
  frozen in the historical result;
- the derived 198-to-192 cleaning counts depend on those historical labels.

No active config, output catalog entry, or future report may present the old
198/192 snapshot as the current PolyBench validation input. Historical method
documents may continue to call the old Plan-Check-Code workflow `PCT`; new work
uses `PCE` for Plan-Code-Evaluate.

## New Source Universe

The replacement data collection starts from all 199 Python instances in one
explicitly frozen revision of `AmazonScience/SWE-PolyBench`, not from the 198
instances successfully processed by the old pipeline. The source snapshot must
record the dataset revision, task-list hash, original row hash, `repo`,
`base_commit`, Dockerfile hash, test command, task category, and language.

Image availability is an environment fact, not a label. A task whose official
image cannot be acquired is recorded as `IMAGE_UNAVAILABLE_V1_1`; it is not
converted to `resolved=false`. Coverage and exclusions are reported by
repository and task category.

## Exact Image Contract

Formal PCE and Checker evaluation use only the official instance-level
`ghcr.io/timesler/swe-polybench.eval.x86_64.<instance_id>:v1.1` image. They do
not silently use `v1.0`, `latest`, or a locally built substitute.

Image preparation writes an incremental provenance manifest. A newly pulled
image records the GHCR OCI digest before and after the pull, requires those
observations to agree for `pull_attested` provenance, and records the source
reference, SIF path, SIF size, and SIF SHA-256. Cached images can be audited
retrospectively, but that weaker record must remain labelled `retrospective`:
it observes the current tag digest and SIF hash after the original pull and
does not claim to reconstruct the pull-time digest.

The manifest is frozen before PCE. Later execution consumes the reviewed exact
digest/SIF identity; version fallback is not a runtime responsibility.

### Image-preparation operation

The Python-199 preparation is a serial login-node operation, not a Slurm array
and not a PCE phase. A frozen remote JSON supplies the ordered 199
image references. For every reference, the preheater writes to a PID-qualified
temporary SIF and atomically renames it only after a successful pull. It writes
an incremental provenance record after every cached, pulled, or failed image.
The completed operation used one attempt per image and a six-hour per-image
upper bound. Targeted login-node retries used the same exact `v1.1` policy.

Manifest schema 2 keeps the original 199-reference universe when a targeted
retry selects only a subset, records each invocation under `runs`, preserves
prior failure evidence, and declares `complete` only when every requested
reference has a terminal record. Re-auditing an existing successful SIF adds
`last_verified_*` fields without replacing stronger `pulled/pull_attested`
provenance with a retrospective record. This makes targeted retry and audit
safe at the evidence layer; it does not make a failed registry response
successful or permit another image tag.

## New PCE Evidence Generation

For each image-available task, Plan, Code, and Evaluate run as separate Agent or
evaluator phases in fresh containers derived from the same frozen SIF. Phase
resume occurs only after a completed durable phase output; an Agent conversation
is never resumed from an internal step.

Fresh-container isolation is supplemented by a Git baseline boundary. Before
each Plan or Code Agent, and again before Evaluate, the workflow checks that the
dataset-declared `base_commit` exists, runs `git reset --hard <base_commit> &&
git clean -fd`, and verifies exact `HEAD` plus an empty non-ignored worktree.
PCCE applies the same boundary before each Checker and Planner revision. Raw
before/after evidence is stored outside the repository workspace. The complete
113-SIF audit is recorded in
`docs/reference/polybench_base_commit_audit_20260824.md`.

The first real-Apptainer boundary smoke exposed that `--writable-tmpfs` does
not make a root-owned `/testbed/.git` writable to an unprivileged Iris user.
Plan failed while creating `.git/index.lock` before any LLM call. The corrected
runner therefore materializes `/testbed` from the frozen SIF into a fresh,
phase-local host directory and bind-mounts that user-owned directory for Plan,
Checker, Planner revision, Code, and Evaluate. Baseline restore and verification
run only after materialization; the phase workspace is discarded after its
durable checkpoint, while baseline evidence remains outside it.

The raw PCE record preserves, where available:

- frozen dataset row and image provenance identity;
- project Git commit and source/config/prompt hashes;
- model, API provider/base, temperature, runtime versions, token usage, cost,
  timestamps, and provider model fingerprint;
- complete Plan and Code trajectories and final submissions;
- generated plan and patch with hashes;
- evaluator command, patch-application evidence, raw output, parsed F2P/P2P,
  resolved label, timing, and failure category;
- every infrastructure attempt without exposing retries as research evidence
  to later Agents.

Code may create temporary diagnostic tests, but the Agent owns the choice of
which implementation changes to stage for submission. PolyBench supplies its
own official test patch, so Code is explicitly instructed to leave every test,
fixture, and temporary diagnostic change unstaged even when the approved plan
asks it to create and run tests. The Host preserves the staged implementation
patch byte-for-byte and does not remove paths or repair the selection. Before
workspace cleanup it separately freezes staged paths, the unstaged tracked diff,
the untracked path list, and final repository status as diagnostic evidence.
Diagnostic changes that the Agent intentionally leaves
unstaged remain visible only in its trajectory. A poorly selected staged patch
continues to the clean Evaluator and normally becomes unresolved evidence.
The new smoke derives from the formal Online Code prompt but adds the
PolyBench-specific implementation/test split above. The Agent inspects all
changes, clears staging, stages only its implementation submission, and returns
`git diff --cached --binary --full-index`. The Host does not append `git add -A`.
An intentional empty staged diff is a valid Code result and
reaches Evaluate as `empty_generation`/unresolved; it is not an operational
retry.

Classification separates `task_outcome` (`resolved`, `unresolved`, or
`unknown`), a stable `outcome_reason`, and an independent
`retry_disposition`. Parsed test failures, empty submissions,
code-patch application failure, and code-test timeout are terminal unresolved
outcomes. Frozen-test-patch, parser, container, provider, and infrastructure
failures remain unknown rather than manufacturing an unresolved label.
Transient failures may retry; identity/configuration failures block.
Repository command-output decoding failures are transient execution failures,
not frozen-input validation failures; they retry from the first incomplete
phase while preserving the original failed attempt.

Patch methods are preflighted before mutation, so a fallback never operates on
a tree partially changed by a previous failed method. Every method records its
preflight/application output, repository status, and full binary diff. F2P and
P2P are parsed strictly; malformed lists block before scoring instead of
silently becoming empty sets.

The independent implementation lives under `src/polybench_pce/` and is entered
through `scripts/run_polybench_pce_hpc.py`. It does not call or modify the
Online GEPA rollout path. One image-available instance is one uncapped Slurm
array element; Slurm owns scheduling. Within that element, Plan, Code and
Evaluate each receive a fresh Apptainer environment. Completed phase outputs
are identity-bound checkpoints, while a failed phase restarts with a fresh
Agent on the next task attempt. There are three total task attempts. Previous
attempt failures remain raw controller evidence and are not Agent input.

`ulhpc-submit` may stage the same committed source and frozen input under a new
remote workdir on each controller invocation. Run-manifest compatibility
therefore treats absolute staged dataset/image paths as operational locations;
the frozen manifest hashes, image identity, execution fingerprint,
source/config hashes and Git provenance remain authoritative and must match.

After three retryable failed attempts the controller records an incomplete PCE outcome,
the last worker output, the last Slurm status and the evidence directory. It
does not manufacture an unresolved label. Deterministic submission outcomes do
not consume fresh Agent attempts. `final_validation_label` remains null until
the separately reviewed cleaning stage.

`scripts/tools/freeze_polybench_pce_source.py` freezes the official CSV,
dataset revision, complete original rows, per-row identity, Dockerfile hash and
ordered instance universe. `src/polybench_pce/dataset.py` joins that snapshot
to exact lowercase `:v1.1` provenance records. Image records explicitly marked
failed are retained as availability evidence and excluded from execution
without becoming labels.

The code path has local deterministic tests and an independently identified
two-instance platform-smoke config. The earlier smoke identities retained the
Evaluator ordering, patch-policy, classification and walltime defects that led
to their replacements; they remain diagnostic evidence and are not formal PCE
data.

At that stage, the failed batch could not be retried unchanged. Although its Plan and Code
outputs are durable, the current checkpoint identity includes the complete PCE
execution fingerprint, including Evaluator source. A fixed Evaluator therefore
could not silently resume the old run. An Evaluate-only reuse required a new run
identity and an explicit, hash-checked provenance link to the old Plan/Code
checkpoints. That migration path was subsequently implemented as the explicit
evaluator-repair workflow documented below. Existing historical PCT code is not
the authority for the current evidence.

A complete post-fix smoke now uses the separate
`hpc-smoke2-evaluator-fix-20260812` root. Controller `5647573` submitted fresh
worker array `5647574` with fingerprint `b96bc352…`; both workers started from
Plan and do not consume pre-fix checkpoints. The outer submission manifest
binds this launch to commit `84ddf32…`. The inner PCE manifest currently records
a null Git head because `.git` is excluded from remote sync; formal PCE must
explicitly propagate the outer commit identity rather than infer it remotely.

That smoke exposed a second independent issue: both Code submissions changed
the same test file already changed by the official `test_patch`. The official
patch and source hunks applied, proving this was neither a wrong frozen base nor
a malformed diff. PCE now preserves the raw submission, filters diagnostic test
paths, and evaluates the separately recorded implementation patch. Empty and
non-applicable code patches follow official PolyBench scoring semantics and
complete as unresolved instead of being retried as operational failures.
These semantic changes use the new smoke identity
`hpc-smoke3-patch-policy-outcomes-20260812`; neither earlier checkpoint tree is
eligible for implicit resume.

That smoke completed one instance in about 48 minutes and lost the other to the
inherited 55-minute Slurm limit during Plan. The next isolated smoke identity is
`hpc-smoke4-walltime125-resume-boundary-20260813`. It uses `1 CPU / 4G` and a
125-minute hard upper bound. Normal completion releases the allocation
immediately; reaching the hard limit relies on Slurm reclamation and does not
require a final application-managed save or cleanup interval. The worker now
writes an identity-bound atomic checkpoint after all method-relevant evidence
for each phase is complete and before environment/workspace cleanup. Plan saves
the final plan and trajectory; Code additionally preserves the exact staged
submission and its hash without Host transformation; Evaluate saves the official
parser, score/classification, and raw evidence. Cleanup remains worker-owned and
best-effort. A cleanup error is audited but cannot invalidate the checkpoint;
an interrupted worker therefore resumes from the first genuinely incomplete
phase without resuming an Agent conversation.

This automation boundary does not select a preferred stochastic result. If an
incomplete phase must run again, each execution is treated as an independent
draw from the same distribution; being a later attempt is assumed not to alter
that distribution. Completed phases are never redrawn merely because cleanup
or later orchestration failed.

The final platform smoke completed end to end under
`hpc-smoke4-walltime125-resume-boundary-20260813`. Worker array `5661319`
completed both instances on attempt 1 with all Plan, Code, Evaluate and final
outputs durable. A later controller job, `5670870`, reused those outputs,
submitted no new worker, and completed collection in four seconds. The final
controller result is 2/2 complete, 0 incomplete, 1 resolved and 1 unresolved.
This validates transport, phase isolation, checkpoint-before-cleanup and
controller collection for the smoke scope; the two labels remain test-only
evidence and do not enter the PolyBench generalization dataset.

## Cleaning And Validation Snapshots

Data cleaning begins only after the new raw PCE snapshot is complete. Its
policy must be stated and versioned independently of all guideline predictions.
The previous 198-to-192 decisions and counts are not carried forward as a
result. Raw evidence is retained, every exclusion has a deterministic reason
and evidence pointer, and cleaned/category views are derived without new LLM
calls.

After data and image manifests are frozen, the existing additive
`src/offline_check_only/` path may be configured for the new snapshot. It
reuses the current Checker and one-Agent-per-Slurm-task transport without GEPA
or Reflection. The frozen 111-case snapshot now satisfies the data prerequisite;
an active PolyBench check-only config has not yet been created.

The guideline prerequisite is now also frozen. The tracked bundle is
`configs/frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/`
with content SHA-256
`0e1f8d7bd876c4d7f1760c1729ccb456afbc7034a34ba8f2f88d8f29478c85d6`.
It contains five exact strings selected before any new PolyBench Checker
output: the common seed; minibatch-eight candidates 1 and 2; and train-only
3x3 candidates 3 and 6. Its manifest binds every text to the completed source
run's candidates, candidate metrics, run manifest and progress hashes.

The guideline bundle originally predeclared `seed`, `b8_candidate_2`, and
`b3x3_candidate_6` as primary inputs, with `b8_candidate_1` and
`b3x3_candidate_3` as reserves. The first clean PCCE stage ran Seed and
`b8_candidate_2`, then paused after neither improved the paired PCE baseline.
Because that decision followed inspection of their PolyBench outcomes, any
future candidate-6 or reserve evaluation would be exploratory. It cannot be
used to select a confirmatory external-validation winner, and no frozen
guideline may be modified in place.

The formal PCE run is now complete. Its 113 exact-`v1.1` source cases produced
111 parsed-test outcomes, one Evaluate test timeout and one Code-stage
incomplete outcome after three attempts. The validation source policy keeps
only the 111 parsed-test outcomes:

- `huggingface__transformers-8747` is recorded as `PCE_INCOMPLETE`; three fresh
  Code attempts each reached the 1800-second command limit without producing a
  Code checkpoint or an evaluable patch.
- `huggingface__transformers-12981` is recorded as
  `TEST_EXECUTION_TIMEOUT`; Plan and Code completed, but the official Evaluate
  command reached its 1800-second limit without parsed F2P/P2P evidence.

Neither case is converted to unresolved. Their complete raw PCE records remain
in the locally mirrored formal run.

The subsequent plan cleaning reuses the exact conservative policy that reduced
the historical SWE-bench Verified source from 500 to 482. It excludes a plan
only when the PCE outcome is resolved, the evaluated implementation patch is
non-empty, and the plan is one of three high-precision placeholder forms:
`EXACT_PLACEHOLDER`, `GENERIC_PLACEHOLDER`, or `PATH_ONLY_PLAN`. Unresolved
placeholder plans remain valid negative examples. Short plans, plans without a
particular section, and plans that name only one relevant file but contain
additional semantic guidance are not excluded. Applying that policy to the 111
parsed-test PolyBench outcomes excluded zero further cases.

The immutable derived snapshot is
`output/SWE-PolyBench/polybench-guideline-validation-datasets/20260815_python111_testparsed_26dad63b5cf3/`.
It contains 59 resolved and 52 unresolved cases. Its ordered instance-ID
SHA-256 is `26dad63b5cf34bc945a0a3363de13becc66f7503895d5ea23feef7cdda56bf29`;
its validation JSONL SHA-256 is
`fcc3d3842e93a403399cd559593e7de7ba9adebacd751685def19e040ca3b328`.
`source_exclusions.json` preserves the two non-test-parsed exclusions, while
`exclusions.json` records the zero placeholder exclusions. The deterministic
builder is `scripts/tools/build_polybench_pce_validation_snapshot.py`.

For every reported view, include accuracy, balanced accuracy, MCC,
class-explicit precision/recall/F1, pass/reject rates, confusion matrix,
timeouts, and operationally incomplete counts. Report seed/candidate prediction
differences without using them to train or revise any guideline.

## Current Preparation State

The historical 198-list preparation remains retired evidence. The replacement
strict-`v1.1` image scan, authenticated review and end-to-end two-instance HPC
smoke are complete. The source-freezing tool now deterministically joins the
official Python-199 CSV to the complete image provenance and refuses missing,
non-terminal or non-`v1.1` availability state.

Frozen smoke inputs and runtime evidence have distinct roots:
`polybench-pce-inputs/` contains immutable staged input, while
`polybench-pce-runs/smoke/` contains controller, task, phase and evaluator
evidence. Future formal evidence uses `polybench-pce-runs/formal/`; download
operations and their incremental manifest remain outside all three run roots.

The strict-`v1.1` image scan completed on 2026-08-12 and its authenticated
review completed on 2026-08-13. Of 199 requested images, 113 are available and
86 are unavailable. Lowercasing the OCI repository component recovered the
single `Significant-Gravitas__AutoGPT-4652` reference. The initial anonymous
scan classified 59 failures as `registry_manifest_not_found` and 27 as
`registry_access_forbidden`. A GitHub `read:packages` credential was configured
interactively on Iris without entering the repository, command arguments, or
logs; all 27 then returned `manifest unknown`. The final strict-`v1.1`
classification is therefore 86 `registry_manifest_not_found`, with no remaining
credential ambiguity and no fallback tag or local build.

The reviewed immutable input is frozen locally at
`output/SWE-PolyBench/polybench-pce-inputs/20260814_python113_v11_8c7d9485d1d0/`.
It contains 113 source rows in original CSV order, the complete 199-record image
provenance, and the 86-record authenticated unavailability evidence. Its
ordered instance-ID SHA-256 is
`8c7d9485d1d077101469e4e80e41b3c009d7fced3e62dd28632d1e9b065d3fc4`;
the source CSV SHA-256 is
`17ad661b20e9af1e2067fbbc2e2658a21137d56693570d1d07f966d8bc0408b7`;
the image provenance SHA-256 is
`0913e4a5a0064e3decce5b01c259973cbb01cbe6ab40fd57f6ca0c5f1ed53afd`;
and the unavailability evidence SHA-256 is
`84b773f0efc2df628fdf6752ce25428a3ec3818d4ace00dc5dc2af08685ede46`.
Selection depends only on exact-image availability (`cached` or `pulled`), not
PCE labels, guideline predictions or smoke outcomes. The loader accepts all
113 unique cases and their strict test metadata. The formal raw-data config is
`configs/polybench_pce_hpc_formal.yaml`, with the independent run identity
`python113-v11-pce-20260814`. The submission entry explicitly propagates the
committed local Git SHA into the remote controller manifest because `.git` is
not synchronized; malformed or missing explicit identities cannot silently
become another commit. Formal controller `5671976` submitted worker array
`5671978` for all 113 cases and yielded successfully; its manifest records
commit `10ff821da6060b78061b93b88ff32dea955a2bf8` and execution fingerprint
`bbb38332871505fe4542076ad817832727897e4c42e57a822dea025001097306`.
The formal run finished with 112 workflow-complete tasks and one attempt-
exhausted incomplete task. Its complete raw evidence was mirrored locally on
2026-08-15. The separately frozen validation snapshot uses only the 111
test-parsed outcomes and applies the historical conservative placeholder policy
without additional exclusions.

An evaluator audit on 2026-08-18 found that three of those 111 “test-parsed”
outcomes had shell return code 127: a host `~/.local/bin/pytest` entry was
visible inside Apptainer but its interpreter no longer existed. Those commands
did not execute, so the 59/52 derived label snapshot is frozen for provenance
but is not score-usable until Evaluate is rerun from the preserved Plan/Code
evidence under the clean-environment runtime. PolyBench validation remains
held out from guideline training throughout this repair.

The evaluator-only recovery entry is
`scripts/hpc_submit_polybench_pce.sh --resume-evaluator <repair-id>`. It creates
an independent repair manifest and task batch under the original run's
`evaluator_repairs/` directory. A case is eligible only when both completed Plan
and Code checkpoints exist and pass source identity/schema validation. Their
payloads are copied unchanged as JSON values under the repair identity;
missing Plan/Code completion is recorded as skipped, while malformed or
identity-mismatched checkpoints block the repair. The ordinary PCE runner then
loads those checkpoints and starts at Evaluate, so resumed Evaluate and direct
Evaluate over the same fixed Plan/Code use the same evaluator path. Original
outputs remain the provenance authority for the pre-fix run; repaired outputs
are never silently written over them.

The first repair diagnostic, `cleanenv-nohome-20260818`, completed 112 Evaluate
workers in one attempt and eliminated shell return codes 126/127 and host
Python/site-package initialization failures. It also exposed that Apptainer's
`--no-home` suppresses the home bind but does not replace the inherited `HOME`
value: tests still saw the nonexistent or unwritable `/home/users/twang`,
causing cache/path failures and contaminating multiple labels. Its provisional
65/47 resolved split is diagnostic only and must not become the held-out
validation authority.

The first writable-HOME attempt, `isolated-home-smoke-20260820`, used a normal
bind plus `--env HOME=/tmp/vibe_home`. All seven targeted Evaluate tasks
completed and retained byte-equivalent Plan/Code inputs, while 126/127 and host
`~/.local` injection disappeared. However, all five HOME-sensitive cases still
used `/home/users/twang` or attempted to create `/home/users`. Apptainer 1.2.1
treats HOME specially: runtime environment overrides cannot replace it, and the
runtime resets it to its computed `homeDest`. This run is diagnostic only. The
corrected runtime uses native
`--home <phase-local-host-temp>:/tmp/vibe_home`, which makes the isolated mount
the runtime `homeDest` rather than trying to override HOME afterward.

The targeted repair filter is exposed as repeatable
`--resume-evaluator-instance INSTANCE_ID`. The completed failed diagnostic is
`isolated-home-smoke-20260820`; its corrected successor is
`isolated-home-native-smoke-20260820`. Both evaluate the five directly affected
cases
`langchain-ai__langchain-5450`, `huggingface__transformers-30899`,
`huggingface__transformers-29449`, `huggingface__transformers-31448`, and
`yt-dlp__yt-dlp-5933`, plus resolved/unresolved controls
`langchain-ai__langchain-4579` and `keras-team__keras-19863`. Acceptance requires
all seven eligible checkpoints to start directly at Evaluate, no host-home or
126/127 evidence, no HOME/cache permission failure, and unchanged copied
Plan/Code payloads. Labels are diagnostic until those runtime checks pass.

The corrected full repair `isolated-home-formal-repair-20260820` completed on
2026-08-21. It reused 112 validated Plan/Code checkpoints, produced 111 parsed
test outcomes and one semantic test timeout, and reported 73 resolved / 39
unresolved workflow outcomes. For labelled validation, only the 111 parsed
outcomes are retained. `huggingface__transformers-8747` is excluded because no
completed Code checkpoint exists; `huggingface__transformers-12981` is excluded
because its test command reached the evaluator's 1800-second limit without a
parsed result. The frozen decision and operational evidence are recorded in
[`reference/polybench_pce_cleaning_20260821.md`](reference/polybench_pce_cleaning_20260821.md).
This selection does not inspect resolved labels or guideline performance.

The repaired PCCE audit subsequently identified 23 parsed evaluator outputs
with explicit missing model-cache or network-download evidence. Because nine
of those cases nevertheless resolved, dependency preparation must cover the
full 23-case evidence-derived scope rather than selecting only unresolved
outcomes. The frozen case list and cache contract are recorded in
[`reference/polybench_dependency_preheat_scope_20260821.md`](reference/polybench_dependency_preheat_scope_20260821.md).
The repair design leaves official SIFs unchanged, freezes a separate cache,
mounts it read-only into Evaluate only, disables evaluator network access, and
then runs one new PCE and one new PCCE Evaluate-only identity over fixed
Plan/Code evidence.

The formal dependency-cache repair completed on 2026-08-23 for the 22 cases
having both PCE and PCCE Code checkpoints. The resulting overlay is retained as
diagnostic provenance, not as score-usable PCE/PCCE evidence. A subsequent
repository-baseline audit found that the formal Code prompt ended with
`git add -A`, while at least one exact frozen SIF already contained uncommitted
`Dockerfile` and `.dockerignore` differences before the Code Agent ran. PCE and
PCCE Plan, Checker, revision, and Code environments exposed or copied the SIF
working tree without first restoring and verifying `base_commit`. The
Evaluator did restore the base commit, so Agent reasoning and patch extraction
did not share one verified repository baseline.

The materialized comparison under
`output/SWE-PolyBench/polybench-pcce-runs/formal/seed-python111-20260817/comparisons/pce-vs-seed-pcce-updated-20260823/`
is frozen unchanged. Its descriptive counts (PCE 79/111 and seed PCCE 75/111)
must not be used to assess Checker or guideline quality. Evaluate-only repair
remains a valid mechanism for isolating evaluator-network effects when its
Plan and Code inputs are trustworthy, but it cannot repair an Agent-visible
repository mismatch or a contaminated Code submission. The replacement formal
PCE and PCCE identities therefore required an audited clean repository at every
Agent phase, Agent-owned staged patch selection without final `git add -A`, and
a true empty generation path before any Plan/Code evidence was reused.

The replacement raw-data identity is
`python113-v11-clean-boundary-v1-20260825`, configured by
`configs/polybench_pce_hpc_formal_clean_20260825.yaml`. It ran all 113 frozen
exact-`v1.1` inputs through the v3-smoke-accepted clean-repository and
Agent-owned staged-patch boundary. Its initial Evaluate phase retained the
ordinary evaluator behavior. The frozen dependency cache covers only 22
evidence-defined cases, while enabling it makes manifest membership mandatory;
therefore it was not attached to the 113-case initial config. The later
manifest-bound evaluator-only repair preserved Plan and Code bytes, invoked no
LLM, and contributed to the final frozen 99-case authority described below.

The clean-run repair configuration is
`configs/polybench_pce_hpc_dependency_cache_clean_20260825.yaml`. It targets
`python113-v11-clean-boundary-v1-20260825`, retains the exact clean-run
Plan/Code semantics, and changes only Evaluate by binding the frozen dependency
cache read-only and disabling evaluator networking. The subsequent corrected
PCCE receives a new run identity and a 45-minute PC/CE worker ceiling; the
historical `seed-python111-20260817` configuration remains immutable.

The completed clean PCE has 113/113 atomic worker outputs. Exactly 100
cases reached `tests_parsed` (67 resolved and 33 unresolved); the remaining 13
are preserved as operational/source exclusions rather than being assigned an
unresolved label. The conservative resolved-placeholder policy excludes zero
additional cases. The frozen paired snapshot is
`20260825_python100_cleanpce_testparsed_887d4ec9df49`; it contains both the
Checker validation rows and a source-hash-bound minimal projection of the PCE
outputs supplying PCCE's first plans and baseline results. Full trajectories
remain in the raw PCE run rather than entering the PCCE input. This intermediate
100-case snapshot preceded the reviewed 21-case evaluator repair and final
environment exclusion. Corrected PCCE used the resulting 99-case authority,
not this intermediate parent snapshot.

The evaluator-only repair `clean-depcache-v1-20260826` is now complete and
mirrored locally. Its remote and local trees both contain 195 files and
94,850,311 bytes; their ordered file-hash tree digest is identical. All 21
selected outputs are `completed/tests_parsed`: 16 resolved and 5 unresolved.
Relative to the ordinary evaluator, four cases changed from unresolved to
resolved (`transformers-15158`, `16661`, `17082`, and `24238`); no case changed
in the opposite direction. The overlay verifies that every source-row hash and
Plan is byte-identical to the parent PCE evidence and replaces only the
evaluator result.

`transformers-25636` is the one member of the original 23-case dependency-risk
scope whose required `ArthurZ/flax-tiny-random-bert-sharded` fixture returned
401/RepositoryNotFound and could not be frozen. It is therefore excluded by the
predeclared environment policy rather than assigned a repaired label. The final
immutable paired snapshot is
`20260826_python99_cleanpce_depcache_03619730229d`: 99 cases, 70 resolved and
29 unresolved. Its parent 100-case snapshot remains immutable provenance.

## First-Stage Outcome And Boundary

The clean paired PCCE stage is complete for the minimal Seed and frozen
minibatch-eight candidate 2. Corrected Seed PCCE resolves 66/99. Candidate 2
resolves 66 of 98 method-complete cases and has one operationally incomplete
PC case; on the common 98-case terminal intersection, PCE resolves 69 and both
PCCE guidelines resolve 66. Candidate 2 therefore does not reproduce its SWE
validation improvement as an end-to-end PolyBench gain.

This stage is closed rather than extended to candidate 6 or reserve guidelines
under the unchanged method. The result is evidence for redesign, not new
Offline GEPA training data. Any prompt, guideline, metric, or workflow change
informed by these outcomes requires a new experiment identity and a new
untouched final holdout for a confirmatory generalization claim. See
[`knowledge/offline-pcce-stage-findings.md`](knowledge/offline-pcce-stage-findings.md)
for the behavior audit and next-design requirements.
