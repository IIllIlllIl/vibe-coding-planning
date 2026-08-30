# Behavioral Offline GEPA Adaptation Boundary

> Status: Stage C v2 completed; media-projected formal 131-case snapshot and
> eight-iteration v2 contract prepared but not launch-authorized
>
> Scope: information flow and minimum project-side changes for Behavioral Plan
> Acceptability v1

## Objective

Reuse the existing Offline GEPA candidate search, instance-level Pareto
selection, random minibatches, 0/1 per-case scoring, checkpoint/resume, and raw
trajectory capture while replacing historical SWE resolution supervision with
developer behavior at the first Plan decision boundary.

The deployment-time task is `ACCEPT` versus `DO_NOT_ACCEPT` for P1. The
repository is an explicitly approximate pre-session proxy. Captured pre-P1
tool results are authoritative when repository content differs. The accepted
development-smoke Checker and Reflection wording is frozen in
`configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml`; the prepared
formal v2 configuration reuses those prompts byte-for-byte.

The initial candidate guideline is the deliberately neutral one-sentence file
`configs/gepa_behavioral_acceptability_neutral_seed.md`. It supplies no
default-accept behavior and no fixed review procedure. It is a GEPA candidate
component, not part of the fixed Checker or Reflection prompt.

## Information classes

### Checker-visible decision information

- task/context from the raw session start through P1, in original order;
- ordinary and system-injected user messages before P1;
- visible assistant responses, including clarification questions;
- pre-P1 tool uses and complete raw tool results;
- P1, the first structured Plan-bearing `ExitPlanMode` tool use;
- repository identifier and frozen temporal proxy commit;
- a fixed declaration that the checkout is an approximate pre-session proxy
  and that conflicting pre-P1 observations take precedence;
- the candidate guideline.

Assistant thinking, the matched P1 result, developer reaction, later Plans,
implementation behavior, label, confidence, split, and reconstruction audit
statistics are not Checker-visible. Numeric proxy age and fallback source are
also withheld: they are acquisition diagnostics, not plan evidence, and could
become dataset-specific shortcuts.

### Controller-only supervision

- `ACCEPT` or `DO_NOT_ACCEPT`;
- high-confidence eligibility and deterministic label-signal provenance;
- train/validation membership, deduplication group, and any later predeclared
  case weight;
- repository-proxy provenance and all source hashes.

The controller compares one valid Checker prediction with the boolean label to
produce the existing per-case 0/1 score. Operationally incomplete Checker calls
remain failures and are not coerced into a behavioral decision.

### Reflection-only evidence

- the expected behavioral decision and candidate score;
- the complete Checker output and Checker trajectory;
- the matched P1 approval/rejection result;
- the following developer prompt and other non-thinking post-boundary events;
- later Plans from the same captured session, their order, matched approval
  results, and deterministic P1-to-later-Plan comparison data when constructed;
- repository-proxy provenance and conflict/risk flags needed to diagnose
  whether the Checker over-trusted the approximate checkout;
- stable pointers and hashes for full raw evidence retained outside the prompt
  bundle.

Later evidence explains the P1 label; it never replaces P1 with a revised Plan
or changes which Plan the Checker classified. Assistant thinking remains
excluded from Reflection under the frozen Stage-2 policy.

### Operational and audit-only information

- raw transcript and case-file paths;
- mirror paths, proxy tree, branch-ref availability, time gap, and candidate
  selection provenance;
- acquisition and cleaning manifests;
- task attempts, journals, resource usage, and retry state.

These fields support reproduction and diagnosis but do not become model input
unless a later explicit design decision promotes a named field.

## Snapshot boundary

Behavioral must use a new immutable snapshot schema rather than overloading the
old `resolved`/ASI record:

```text
case identity + split
  checker_input
    pre_p1_context
    proposed_plan_p1
    repository_proxy
  supervision
    decision
    confidence / signal provenance
  reflection_evidence
    p1_result
    developer reaction
    later same-session Plans and results
    controlled post-boundary trajectory
  audit_provenance
```

The snapshot builder should join the frozen Stage-2 cases, repository-
availability cleaning, temporal-proxy manifest, and a separately frozen split.
It must validate that no post-boundary field is nested under `checker_input`.
Proxy selection is already label-free and must remain independent of behavioral
supervision.

## Implemented information-flow foundation

### New Behavioral data model and loader

`build_swe_chat_behavioral_gepa_snapshot.py`, `behavioral_models.py`, and
`behavioral_dataset.py` add a deterministic frozen-input join, Behavioral case
type, and strict loader instead of changing the meaning of
`GEPACase.resolved` in place. The loader needs exact key allowlists, label and
confidence validation, proxy-manifest binding, split-disjoint IDs, and a
`checker_payload()` that returns only the decision-time projection.

The historical `dataset.py` path remains usable for frozen Offline
reproduction. Config and CLI dispatch select the Behavioral loader and runner
only through explicit `task.semantics`; task semantics are never inferred from
field presence.

### Repository execution backend

`behavioral_repository.py` now materializes an exact proxy commit as a clean,
detached, disposable shared-object clone without mutating the preheated bare
mirror. Semantic snapshot identity stores only a relative mirror path; the
configured repository root supplies host-specific operational policy.

The separate Behavioral Checker runner uses the temporal-proxy materializer
and mini-swe-agent's local environment. It does not enter the historical
`DockerChecker` path.

Because mirrors are immutable shared acquisition artifacts, concurrent Checker
calls should not attach ordinary Git worktrees that mutate shared mirror
administrative state. A per-call shared-object clone or another isolated
materialization is safer. No Docker, Apptainer, image derivation, build, or test
environment reconstruction is required for Behavioral v1.

### Checker task and output semantics

`BehavioralCheckerOutput` uses `predicted_accept` and never silently calls the
new value `predicted_resolved`. It retains decision reason, repository evidence,
and complete trajectory. The smoke Checker prompt changes only the task and
input boundary: the proxy is supplementary evidence, while conflicting
pre-decision transcript observations are authoritative. Review strategy remains
owned by the candidate guideline. Checker execution, bounded per-command
timeout, output validation, instance-template mapping, and Slurm task retry are
implemented.

### Adapter and metric traces

`BehavioralGEPAAdapter` reuses GEPA's `EvaluationBatch`, one `rules` component,
scalar score, optional balanced-accuracy weighting, train repetitions, and
reflective-dataset interface while using `observed_decision` and
`predicted_accept`. The Behavioral case's separate worker projection contains
repository materialization data but no label or Reflection evidence.

Evaluation caching, retry/audit behavior, label-free HPC task manifests,
Behavioral batch execution, resume identity, and formal reporting are wired
through the retained GEPA runner semantics.

Accuracy and balanced-accuracy support can be reused mechanically after the
positive class is defined as ACCEPT. The balanced 8-case development smoke uses
accuracy as its primary mechanical score and balanced accuracy as a diagnostic.
The prepared formal v1 run keeps accuracy as its search metric and requires
balanced accuracy, MCC, both-class precision/recall, confusion matrix, pass
rate, and incomplete count as diagnostics.

### Reflection evidence writer

`EvidenceBundleWriter` now has a `behavioral_acceptability` mode. It writes one
structured
case directory containing Checker output/trajectory, behavioral label,
immediate P1 result, developer reaction, later Plans/results, controlled
post-boundary events, and proxy-risk metadata. It must not expect historical
Plan/Code trajectories, generated patches, or evaluator results.

Behavioral traces and bundles call the supervision fields `observed_decision`
and `observed_accept`, emphasizing that they record developer behavior rather
than an objective plan-quality truth. The smoke Reflection prompt permits
controlled post-decision evidence only for attribution and diagnosis and
forbids promoting it into deployment-time requirements.

The Reflection proposer selects this bundle mode from explicit task semantics.
Behavioral Reflection uses a separate no-container worker; post-boundary
evidence never enters Checker task manifests.

### Runner, HPC worker, and config dispatch

Offline config parses an explicit task semantic plus temporal-Git-proxy
repository and workspace roots. Behavioral runner dispatch selects the loader,
Checker, Adapter vocabulary, reports, and Reflection mode. Its Slurm worker
deserializes a label-free Behavioral payload and uses the Git backend;
supervision, scores, and post-boundary evidence are absent from task files.
Historical Offline retains its Apptainer path.

Run fingerprints must include the new task semantic, dataset manifest,
temporal-proxy manifest, repository backend, prompts, and new source modules. A
Behavioral run always has a new run directory and cannot resume the old
candidate tree.

### Completed development smoke

The smoke used three ordered stages: local no-LLM contracts, four balanced
local Checker/Reflection prompt units, then one full HPC GEPA proposal on a
separate balanced 4-train/4-validation development fixture. All eight exposed
cases are development data and may enter formal train only.

Stage C v2 completed one proposal iteration with 16 logical metric calls, one
accepted candidate, zero incomplete Checker outcomes, and zero audited Checker
leakage. Both seed and proposal scored 0.5 on the four-case development
validation set, so the neutral seed remained best. This proves flow integrity,
not guideline effectiveness.

### Reports and tests

No-LLM tests currently cover deterministic source-to-snapshot construction,
exact split-universe binding, strict snapshot loading, synthetic leakage
rejection, label-free worker projection, acceptability scoring and Reflection
evidence, exact clean detached materialization, disposable workspace cleanup,
the exact neutral seed, and the unchanged focused Offline regressions.

The formal split freezer assigns repository/duplicate components without using
labels: every component containing a Stage-B/C case enters train and every
remaining component enters validation. The frozen result is 84 train cases
from eight repositories and 47 validation cases from 29 repositories, with no
repository, exact normalized Plan/context, or thresholded Plan near-duplicate
crossing the split. Formal validation is GEPA candidate-selection data, not an
untouched final holdout.

Launch preflight found structured PNG base64 embedded in the Checker text for
three cases. The superseding formal v2 snapshot omits only those binary payloads
while retaining deterministic media descriptors and the unchanged frozen raw
authority. No prompt or GEPA search setting changed. A two-extrema Checker
smoke remains required because the projected longest validation context exceeds
the largest prompt exercised in Stage C.

## Components retained unchanged

- candidate guideline as GEPA's single `rules` component;
- per-case scalar score interface;
- random epoch-shuffled minibatches;
- instance-level Pareto candidate selection;
- GEPA evaluation caching once Behavioral payloads enter the fingerprint;
- project-side sampler/RNG checkpoint and resume;
- proposal counting, stopping, audit logs, and raw trajectory retention;
- third-party GEPA core.

The largest implementation change is the repository execution backend and the
semantic renaming of labels/traces, not the GEPA search algorithm.

## Deferred decisions

- how rejection reasons should be classified for analysis without relabeling
  observed behavior;
- what independent data can support a later untouched-generalization claim;
- whether any cases should receive no repository access as an ablation.
