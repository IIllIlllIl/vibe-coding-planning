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

## New PCE Evidence Generation

For each image-available task, Plan, Code, and Evaluate run as separate Agent or
evaluator phases in fresh containers derived from the same frozen SIF. Phase
resume occurs only after a completed durable phase output; an Agent conversation
is never resumed from an internal step.

The raw PCE record should preserve, where available:

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

Only a valid evaluator terminal result creates a resolved/unresolved label.
Image, container, provider, output-contract, patch-transport, parser, and
evaluator-infrastructure failures remain operational outcomes. Their retry
policy may support unattended execution but must not manufacture a label.

The new PCE entry point and frozen-image consumption are not implemented in the
current stage. Existing historical PCT code is not the authority for the new
run.

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
provenance for cached, pulled, and failed images. A new 199-image request and
frozen dataset revision remain prerequisites for formal PCE.
