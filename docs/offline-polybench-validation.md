# Offline Guideline PolyBench Generalization Validation

> Authority: planned external validation contract for the current standalone
> Offline guideline method
>
> Last reviewed: 2026-08-11

## Purpose And Non-Training Boundary

PolyBench is an external generalization evaluation. Its issues, plans, labels,
Checker predictions, trajectories, metrics, and error analysis must never enter
GEPA training, Reflection, candidate proposal, candidate acceptance, guideline
repair, prompt design, metric selection, or stopping decisions.

All guideline-producing experiments that will be compared, including the
current minibatch-eight run and the planned 3x3 design, must finish and freeze
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

Code may create temporary diagnostic tests, but test-file changes are not part
of the evaluated submission. The Code prompt asks the Agent not to stage them,
and a deterministic Host policy independently removes conventional test paths.
Both raw and filtered patches, their hashes, kept/removed/overlap paths, and
the complete trajectory are retained. A test-only submission therefore becomes
an auditable empty generation. The Host does not repair source changes or merge
them with the official test patch.

Classification separates `task_outcome` (`resolved`, `unresolved`, or
`unknown`), a stable `outcome_reason`, and an independent
`retry_disposition`. Parsed test failures, empty filtered submissions,
code-patch application failure, and code-test timeout are terminal unresolved
outcomes. Frozen-test-patch, parser, container, provider, and infrastructure
failures remain unknown rather than manufacturing an unresolved label.
Transient failures may retry; identity/configuration failures block.

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

The code path has local deterministic tests. A separate two-instance platform
smoke config and `ulhpc-submit` controller entry now exist. Controller job
`5642300` completed normally and yielded after submitting worker array
`5642301`. Both workers saved completed Plan and Code checkpoints, then exposed
an Evaluator ordering defect: evaluator-owned patch/script files were written
before `git clean -fd` and deleted before use. The implementation now resets
the fresh base first and writes those inputs afterward; local regression tests
pass, but the fix has not yet passed an HPC Evaluate or full PCE smoke.

The failed batch must not be retried unchanged. Although its Plan and Code
outputs are durable, the current checkpoint identity includes the complete PCE
execution fingerprint, including Evaluator source. A fixed Evaluator therefore
cannot silently resume the old run. An Evaluate-only reuse requires a new run
identity and an explicit, hash-checked provenance link to the old Plan/Code
checkpoints; that migration path is not yet implemented. No formal config is
active against the final Python-199 source snapshot and completed image
manifest. Existing historical PCT code is not the authority for the new run.

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
or Reflection. No active PolyBench check-only config exists until the new PCE
and cleaning inputs pass review.

For every reported view, include accuracy, balanced accuracy, MCC,
class-explicit precision/recall/F1, pass/reject rates, confusion matrix,
timeouts, and operationally incomplete counts. Report seed/candidate prediction
differences without using them to train or revise any guideline.

## Current Preparation State

The first historical 198-list login-node preheat was stopped before completion.
Twenty-six complete `v1.1` SIFs were retained and received retrospective
provenance records; they are preparation evidence, not yet the formal 199-image
manifest. The tracked login-node preheater now records incremental OCI/SIF
provenance for cached, pulled, and failed images. The new PCE implementation,
source-freezing tool and dry-run-validated HPC submission entry are present.
The platform smoke freezes two completed `pull_attested` Transformers images
and source rows; it may run concurrently with the login preheater because it
never selects an in-progress image and the two processes use separate
Apptainer temp directories. Formal Python-199 source and image manifests,
completed image-manifest review, an executed HPC smoke and a separate formal
config remain prerequisites for formal PCE.

Frozen smoke inputs and runtime evidence have distinct roots:
`polybench-pce-inputs/` contains immutable staged input, while
`polybench-pce-runs/smoke/` contains controller, task, phase and evaluator
evidence. Future formal evidence uses `polybench-pce-runs/formal/`; download
operations and their incremental manifest remain outside all three run roots.

The strict-`v1.1` image scan completed on 2026-08-12. Of 199 requested images,
113 are available and 86 are unavailable under the documented anonymous GHCR
access path. Lowercasing the OCI repository component recovered the single
`Significant-Gravitas__AutoGPT-4652` reference. The remaining failures are 59
`registry_manifest_not_found` responses and 27
`registry_access_forbidden` responses. Every one of the 27 forbidden images
failed an actual initial pull; a focused login-node retry reconfirmed that
class. Iris has no configured Docker, Containers, or Apptainer registry
credential, so a credentialed retry requires separate authority and is not
silently attempted.

The operational manifest is complete but is still not a PCE dataset. The
derived `v1.1-unavailable-images-20260812.json` records all 86 exclusions with
their raw download evidence, explicitly leaves `research_label` null, and
states that this is image-availability cleaning only. Its SHA-256 is
`103adf123f2f2fa084197e09436eac78e361dc383c6d23f82c0623d556fec927`.
Formal PCE still requires a reviewed immutable source/image snapshot containing
the 113 available cases.
