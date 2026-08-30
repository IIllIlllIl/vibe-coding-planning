# Behavioral Offline GEPA Adaptation Boundary

> Status: information-flow foundation implemented; formal snapshot, runtime
> wiring, and prompts are not implemented
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
tool results are authoritative when repository content differs. The exact
Checker and Reflection wording is intentionally deferred.

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
reproduction. Config now has an explicit `task.semantics`; the current runner
does not yet dispatch the Behavioral loader, so no formal run is launchable.
Task semantics must never be inferred from field presence.

### Repository execution backend

`behavioral_repository.py` now materializes an exact proxy commit as a clean,
detached, disposable shared-object clone without mutating the preheated bare
mirror. Semantic snapshot identity stores only a relative mirror path; the
configured repository root supplies host-specific operational policy.

The current `DockerChecker` derives a SWE-bench image and restores a benchmark
`base_commit`; it cannot execute mirror-only cases unchanged. Add a separate
Behavioral Checker runner that uses the implemented materializer and exposes
the checkout through mini-swe-agent's local environment.

Because mirrors are immutable shared acquisition artifacts, concurrent Checker
calls should not attach ordinary Git worktrees that mutate shared mirror
administrative state. A per-call shared-object clone or another isolated
materialization is safer. No Docker, Apptainer, image derivation, build, or test
environment reconstruction is required for Behavioral v1.

### Checker task and output semantics

`BehavioralCheckerOutput` uses `predicted_accept` and never silently calls the
new value `predicted_resolved`. It retains decision reason, repository evidence,
and complete trajectory. Checker execution, timeout/validator retry, instance-
template mapping, and final prompt wording remain deferred.

### Adapter and metric traces

`BehavioralGEPAAdapter` reuses GEPA's `EvaluationBatch`, one `rules` component,
scalar score, optional balanced-accuracy weighting, train repetitions, and
reflective-dataset interface while using `expected_decision` and
`predicted_accept`. The Behavioral case's separate worker projection contains
repository materialization data but no label or Reflection evidence.

Evaluation caching, full historical retry/audit behavior, HPC batch execution,
and formal reporting are not yet wired into the runner.

Accuracy and balanced-accuracy support can be reused mechanically after the
positive class is defined as ACCEPT. Choosing the primary metric remains a
separate experiment-design decision.

### Reflection evidence writer

`EvidenceBundleWriter` now has a `behavioral_acceptability` mode. It writes one
structured
case directory containing Checker output/trajectory, behavioral label,
immediate P1 result, developer reaction, later Plans/results, controlled
post-boundary events, and proxy-risk metadata. It must not expect historical
Plan/Code trajectories, generated patches, or evaluator results.

The existing Reflection proposer selects this bundle mode from explicit task
semantics. Its runtime/container assumptions and prompt content are not yet the
Behavioral execution path and remain deferred.

### Runner, HPC worker, and config dispatch

Offline config now parses an explicit task semantic plus temporal-Git-proxy
repository and workspace roots. Runner dispatch must next select the Behavioral
loader, Checker, Adapter trace vocabulary, and Reflection bundle mode. The
Offline Slurm worker currently reconstructs a
label-free historical `GEPACase` and always loads Apptainer; it must instead
deserialize the Behavioral Checker payload and use the Git backend without
placing supervision or post-boundary evidence in task files.

Run fingerprints must include the new task semantic, dataset manifest,
temporal-proxy manifest, repository backend, prompts, and new source modules. A
Behavioral run always has a new run directory and cannot resume the old
candidate tree.

### Reports and tests

No-LLM tests currently cover deterministic source-to-snapshot construction,
exact split-universe binding, strict snapshot loading, synthetic leakage
rejection, label-free worker projection, acceptability scoring and Reflection
evidence, exact clean detached materialization, disposable workspace cleanup,
the exact neutral seed, and the unchanged focused Offline regressions.

Still required: freeze the real split, then report ACCEPT/DO_NOT_ACCEPT
prevalence, confusion matrix, acceptance and
rejection precision/recall, balanced accuracy, MCC, and operationally
incomplete calls with behavioral terminology. Required no-LLM tests include:

- real worker task serialization and Behavioral worker dispatch;
- transcript observations preserved separately from repository files in the
  Checker template;
- resume rejection after any dataset, task-semantic, prompt, backend, or source
  change.

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

- Checker and Reflection prompt wording;
- how the fixed prompt describes dirty/missing/conflicting repository state;
- split and near-duplicate policy;
- primary optimization metric and success thresholds;
- minibatch size, budget, and stopping conditions;
- whether any cases should receive no repository access as an ablation.
