# ACE + Safe PCE on SWE-bench Verified

> Authority: current Safe PCE source/artifact/execution boundary and retained
> historical PCE/PCCE diagnostic evidence
>
> Last reviewed: 2026-09-09

## Purpose

The active workflow regenerates trustworthy Plan-Code-Evaluate evidence for the
ACE stage. It uses direct Plan submission, isolated Plan and Code workspaces, a
new selection-scoped SIF authority, and the official evaluator. It does not
reuse historical Round-1 or quick50 Plans, outcomes, image manifests, or paired
C4/C5 identities as new experiment inputs.

The quick50 and Seed/C4/C5 results below are retained only as failure-analysis
evidence that motivated Safe PCE and ACE-PCCE. They are not the current
baseline or a source of cases for the new experiment.

The implementation is additive under `src/swe_verified_pce/` and
`src/swe_verified_pcce/`. It does not change the retained PolyBench workflow or
third-party GEPA search code. PCE/PCCE results are external evaluation evidence;
they must not be fed back into the completed Behavioral candidate tree.

## Frozen input layers

The source layer is the complete 500-row `SWE-bench/SWE-bench_Verified`
snapshot at revision `91aa3ed51b709be6457e12d00300a6a596d4c6a3`.
`freeze_swe_verified_pce_source.py` preserves each complete official row and a
row hash. The Agent-visible projection is derived separately from this raw
authority.

The image layer is an independently generated SIF manifest. Every usable
record binds the source manifest, exact SIF bytes, requested image reference,
and a successful read-only check that the official `base_commit` exists in
`/testbed`. A retrospective SIF hash is sufficient for development only when
that provenance is declared; it is not represented as an original pull-time
OCI attestation.

The two-case development smoke selection is frozen in
`configs/frozen_swe_verified_smoke/`. Both cases are excluded from quick
validation and from any untouched holdout.

The 50-case quick-validation membership is frozen in
`configs/frozen_swe_verified_quick_validation/`. Starting from all 500 official
rows, the deterministic policy excludes the two smoke cases, chooses the
lowest fixed-seed hash in each of the 12 repositories, then fills the remaining
38 positions by the same global hash rank. It reads no Plan, PCE, or PCCE
outcome. This deliberately coverage-oriented sample is a bounded development
diagnostic, not a prevalence-exact random sample or untouched holdout. The
same exact 50 cases must be used by PCE, neutral-seed PCCE, and C4 PCCE;
acquisition failure may not replace a selected case.

The formal PCE runtime is prepared in
`configs/swe_verified_pce_quick50_v1_20260901.yaml`, with its bounded
supervisor entry alongside it. The missing `pydata__xarray-6744` SIF was
subsequently acquired without substituting membership. The frozen
selection-scoped image manifest contains 50 audited SIF identities, and all 50
official base commits were verified.

The quick50 PCE completed with 50/50 operationally complete outcomes: 41
resolved and 9 unresolved, with no unknown result. Two cases reused their
durable Plan checkpoint and completed on task attempt 2. This is the paired
baseline for both PCCE methods, not an effectiveness result by itself. The
exact `raw_pce_outcomes.jsonl` SHA-256 is
`1f1e4420ec160d89a144a669d6cfc27ba1b131f35f6b76d59cf496c80650e753`.

The neutral-seed PCCE runtime and supervisor completed under
`configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml` and its matching
supervisor config. All 50 cases reached a terminal method result: 34 Plans
passed the first review, 15 passed after revision, and one was rejected after
three reviews. End-to-end outcomes were 37 resolved and 13 unresolved, with no
unknown or operationally incomplete case. Relative to the paired PCE baseline,
the Seed run repaired none of the nine PCE-unresolved cases and changed four
PCE-resolved cases to unresolved. Two PC tasks required a second Slurm attempt;
both completed without changing membership or terminal semantics.

The C4 runtime
`configs/swe_verified_pcce_quick50_c4_v1_20260902.yaml`, with its matching
supervisor config, completed over the same 50 cases. It consumed the same
frozen selection, exact PCE outcome
bytes, image manifest, prompt/runtime sources, and operational policy under a
distinct guideline and run identity. Both PCCE runtimes reject any PCE outcome
or image manifest whose bytes differ from the frozen hashes. C4 resolved 41/50
with no incomplete result: all 41 paired PCE successes remained resolved and
all nine paired PCE failures remained unresolved. Thus C4 avoided the Seed's
four regressions but created no new resolved case.

The subsequent issue-first diagnostic reused the 16 frozen C4 first-review
rejections rather than rerunning Review 1. Fifteen cases reached a terminal
method result and one remained operationally incomplete; the terminal
transitions were nine R-to-R, two R-to-U, two U-to-R, and two U-to-U. This is a
rejected-subset Planner diagnostic, not a second complete quick50 result.

The later C5 safe-U8 development diagnostic deliberately selected the eight
workspace-safe PCE-unresolved quick50 cases. Four Plans passed Review 1 and all
four remained unresolved. The other four frozen Review-1 rejections initially
could not enter revision because the Verified adapter omitted its dataset
identity. The additive recovery fixed that runtime field without rerunning
Review 1; all four replacement Plans later passed review and reached official
evaluation, producing two resolved and two unresolved outcomes. Combined C5
safe-U8 is therefore two resolved and six unresolved. Because membership was
selected using the PCE outcome and the same cases informed prior C4/C5
development, this result is mechanism evidence only, not held-out effectiveness
or a rate estimate.

### PCE development smoke contract

The first paid execution is PCE-only and contains exactly
`astropy__astropy-12907` and `django__django-10097`. Selection was fixed before
new Plan, Code, or evaluator outcomes were observed. Two repositories exercise
the same current prompts and runtime without turning the smoke into a quality
comparison.

Each case is one independent `1 CPU / 4G / 45min` array task with at most three
fresh Slurm attempts. The supervisor uses `1 CPU / 4G / 10min` controller
slices, polls every five minutes, and stops after a maximum of twelve submitted
slices or an earlier terminal workflow state. This bounds compute by case and
attempt rather than imposing an Agent step, cost, or evaluator deadline.

The smoke passes only when both cases produce completed PCE records with
non-empty Plans, durable Plan/Code/Evaluate checkpoints, preserved raw Agent
and evaluator evidence, and a terminal `resolved`, `unresolved`, or `unknown`
outcome. A legitimate unresolved result is not failure; an operationally
incomplete case is. The audit must also confirm exact input identity,
base-commit restoration evidence, no benchmark-label leakage into Agent
phases, no credential artifact, and resource usage consistent with the
request. The smoke makes no effectiveness or generalization claim and does not
run PCCE or GEPA.

After explicit launch authorization, the prepared persistent supervisor entry
is:

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py start \
  --launch-config configs/archive/supervisor_launches/swe_verified_pce_smoke_supervisor_v1_20260901.yaml
```

With no retry, worker execution is bounded by one 45-minute allocation plus
queue and five-minute supervisor polling latency. Three exhausted task
attempts can extend one case to 135 allocated minutes; the two cases are
independent array elements and may overlap. No fixed elapsed-time estimate can
include queue delay.

## Information boundary

| Phase | Visible benchmark information |
|---|---|
| Plan | issue text and repository/image/base-commit identity |
| Code | issue text, accepted/generated Plan, and repository state |
| Checker | issue text, proposed Plan, guideline, and repository state |
| Plan revision | issue text, previous Plan, Checker feedback, and repository state |
| Evaluate | complete official row, generated patch, test patch, and official test lists |

Gold patches, test patches, and `FAIL_TO_PASS`/`PASS_TO_PASS` never enter an
Agent prompt. PCCE PC task manifests physically remove those fields and the
baseline PCE outcome. A PCE worker owns Plan, Code, and Evaluate for one case,
so its internal task manifest retains evaluator input; the phase renderers are
the enforced Agent boundary.

PCCE always uses the exact first Plan from the newly generated paired PCE
record. A PCE evaluator outcome of `unknown` does not remove an otherwise
complete Plan from the predeclared PCCE membership. The baseline outcome stays
null and is reported separately.

## Execution and outcome semantics

Plan, Code, Checker/revision, and Evaluate use fresh phase-local Apptainer
workspaces restored to the official base commit. Every worker phase uses one
`1 CPU / 4G / 45min` Slurm array element with three total task attempts. Agent
step, cost, and process-level phase deadlines are disabled; Slurm owns the
walltime. The loose per-command timeout remains the only in-process timeout.

Plan, Code, revised Plan, and Checker submissions are checkpointed immediately
after valid Agent submission. Diagnostic collection and cleanup occur after
that resume boundary and cannot cause the completed Agent to run again.
Evaluate writes an identity-bound start marker before official grading.

Evaluation uses the official SWE-bench `make_test_spec` and `get_eval_report`
path. Outcomes are ternary:

- `resolved` and `unresolved` are terminal scored results;
- `unknown` preserves grading uncertainty or evidenced exhausted evaluator
  Slurm timeouts;
- command-launch, repository-reset, and runtime failures remain operational
  failures and follow the bounded retry policy.

Empty or unappliable generated patches are terminal unresolved. The evaluator
has no independent test deadline. Only when all three Slurm attempts terminate
with `TIMEOUT`, and durable checkpoints prove that Code had completed and
Evaluate had started, does controller collection record terminal `unknown`.
Other exhausted tasks remain operationally incomplete. Paired comparison uses
the intersection with a non-unknown terminal result from PCE, neutral-seed
PCCE, and C4 PCCE; the full predeclared membership and every exclusion remain
reported.

## Development order

The required order is:

1. no-LLM contract and regression tests;
2. audit the two selected SIFs and base commits on Iris;
3. explicitly authorized two-case PCE smoke;
4. paired neutral-seed and C4 PCCE smoke using those exact new PCE plans;
5. freeze an outcome-independent 50-case quick-validation selection excluding
   all smoke cases (complete);
6. acquire and audit every selected SIF, then freeze the selection-scoped image
   manifest (complete);
7. run paired PCE, Seed PCCE, and C4 PCCE under immutable run identities
   (complete);
8. retain the issue-first and C5 safe-U8 runs as explicitly outcome-exposed
   development diagnostics, not extensions of the quick50 comparison.

Smoke checks pipeline correctness only. The quick validation is a bounded
generalization diagnostic, not an untouched final holdout.

## Recovered-Plan CE replay

The development-only `verified-train10-shell-recovery-ce2-v1-20260911`
replay isolates two historical Plans whose Markdown backticks were removed by
shell command substitution while `/tmp/plan.md` was written. The intact Plan
text is recovered as an exact substring of the preserved Planner trajectory;
it is not regenerated or repaired by another model. Its frozen manifest binds
the corrupted Plan, recovered Plan, source trajectory, selection, official
source snapshot, and selection-scoped SIF identities by SHA-256.

`src/swe_verified_pce/plan_replay.py` reuses the mature Verified PCE Slurm
transport and worker. It preloads the recovered text as the identity-bound Plan
checkpoint, so only Code and the official evaluator run. Plan and Checker model
calls are forbidden by construction. The resulting outcome is an independent
Code sample from the recovered Plan: it neither overwrites the historical PCE
label nor represents Checker-mediated improvement. Operational failure remains
distinct from unresolved, and exhausted evidenced evaluator timeouts retain the
existing `unknown` policy.

The runtime is
`configs/swe_verified_recovered_plan_ce2_v1_20260911.yaml`; its paired local
supervisor is
`configs/archive/supervisor_launches/swe_verified_recovered_plan_ce2_supervisor_v1_20260911.yaml`. Each of
the two Slurm array elements requests `1 CPU / 4G / 45min`, with three total
operational attempts and five-minute supervisor polling. The authorized replay
completed both cases on 2026-09-11 with two terminal `resolved` outcomes and no
operationally incomplete case. In particular, `astropy__astropy-14539` changed
from historical PCE-unresolved to resolved under an independent Code sample
from the recovered Plan. This is evidence that the shell-corrupted Plan was a
material data defect; it is not Checker-mediated U-to-R evidence and does not
replace either historical label.

## Safe PCE Plan-artifact boundary

The prepared Safe PCE successor retains the Verified source/SIF authorities,
one-case-per-array-worker execution, phase-local Apptainer workspaces,
base-commit restoration, atomic Plan/Code/Evaluate checkpoints, bounded task
attempts, and official evaluator. It changes the Planner artifact boundary;
it does not reinterpret or resume the historical PCE run.

During repository exploration the Planner continues to issue one ordinary
shell action at a time. At completion it returns `FINAL_PLAN` on its own line
followed directly by the standalone Markdown Plan. A project-local
DefaultAgent adapter recognizes that terminal response before the shell action
parser, so Plan code fences, backticks, `$()` expressions, quotes, and Jinja
fragments are never executed or reconstructed through a shell command. No
third-party source is modified.

The marker-stripped text is the Plan authority. Safe PCE rejects a legacy
stdout/file submission and retries the Planner; a temporary file can never
override submitted text. The atomic Plan checkpoint records the exact text,
its SHA-256, the direct-submission protocol, and the raw trajectory. Before
Code starts, the Host verifies the stored hash. Code then receives only the
issue and that exact Plan string in a fresh base-commit workspace; no Planner
workspace, environment variable, or `/tmp` artifact crosses the phase
boundary.

Safe PCE Plan and Code workspaces keep network access for development
diagnostics, but the Apptainer execution boundary rejects remote Git
operations (`clone`, `fetch`, `pull`, `ls-remote`, `remote update`, and remote
submodule update). Local history already present at the frozen base commit
remains available. Both prompts direct the Agent to use the SIF's existing
project environment, trying `/opt/miniconda3/envs/testbed/bin/python` first,
and prohibit installing or upgrading the target repository. This is a
conservative accidental-leakage blacklist, not a complete network-isolation
claim; future smoke evidence must record any blocked command and any remaining
package/network behavior.

Initial Safe PCE planning and ACE-PCCE replanning now share the same core role:
independently inspect repository evidence, preserve the issue objective, and
produce a complete standalone Plan with implementation locations, behavioral
changes, compatibility constraints, and focused validation. Their task-local
differences remain explicit: initial planning has no developer concerns and
returns only a Plan, whereas ACE replanning answers active concerns as well as
returning the revised Plan.

The launch-unauthorized two-case contract is
`configs/swe_verified_safe_pce_smoke_v1_20260911.yaml`, its Planner prompt is
`configs/prompts/swe_verified_safe_pce_planner_v1_20260911.yaml`, and its
supervisor is
`configs/archive/supervisor_launches/swe_verified_safe_pce_smoke_supervisor_v1_20260911.yaml`. It reuses
the already outcome-exposed historical development-smoke membership solely to
test transport and execution. A successful smoke requires direct terminal
responses, exact preservation through the Plan checkpoint and Code handoff,
and two operationally complete evaluator outcomes; it makes no quality or
generalization claim.

The repository-boundary successor keeps the runtime authority at
`configs/swe_verified_safe_pce_boundary_smoke_v2_20260911.yaml`. Its failed v2
supervisor launch is archived without mutation. The reviewed replacement is
`configs/archive/supervisor_launches/swe_verified_safe_pce_boundary_smoke_supervisor_v3_20260911.yaml`:
it omits remote storage paths, accepts the shared supervisor defaults, and
enables inactive submission-copy reclamation. Its presence does not authorize
launch.

The prepared human-audit successor expands this boundary probe to ten
development-only cases from nine repositories. Selection is driven by distinct
Safe PCE transport, environment, planning, compatibility, and implementation
risk coverage rather than historical R/U balance. It is frozen in
`configs/frozen_swe_verified_smoke/swe-verified-safe-pce-audit10-v3-20260912.json`
and runs through `configs/swe_verified_safe_pce_audit10_v3_20260912.yaml`.
Selection spans transport corruption, environment and remote-Git behavior,
apparently sufficient Plans with both outcomes, visible blockers with both
outcomes, compatibility risk, uncertain repository-dependent scope, competing
output contracts, edge semantics, and symbolic behavior. This is richer
manual-audit material, not a quality estimate or held-out evaluation.
Historical outcomes are known because the cases came from prior failure
analysis, but they are not runtime inputs or success criteria. Before launch,
the ten existing SIFs require a new selection-scoped byte-hash and base-commit
audit; the runtime intentionally does not bind a quick50 or other historical
run manifest.
