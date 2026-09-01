# SWE-Verified PCE/PCCE Generalization Workflow

> Authority: the independent SWE-Verified Plan-Code-Evaluate and paired
> Plan-Check-Code-Evaluate data, phase, and evidence contract
>
> Last reviewed: 2026-09-01

## Purpose

This workflow tests whether the Behavioral guideline transfers to the
authoritative SWE-bench Verified task distribution. It regenerates the first
Plan with the current project PCE prompts, then compares paired PCCE runs using
the neutral Behavioral seed and frozen candidate 4. Historical Round-1 plans
are not reused because their planning prompt and runtime identity differ.

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

The neutral-seed PCCE runtime and supervisor are prepared in
`configs/swe_verified_pcce_quick50_seed_v1_20260901.yaml` and its matching
supervisor config. The runtime rejects any PCE outcome or image manifest whose
bytes differ from the frozen hashes, while C4 must later consume these same
paired inputs under a distinct run identity.

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
  --launch-config configs/swe_verified_pce_smoke_supervisor_v1_20260901.yaml
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
7. run paired PCE, seed PCCE, and C4 PCCE under new immutable run identities
   (PCE complete; Seed prepared; C4 not yet prepared or launched).

Smoke checks pipeline correctness only. The quick validation is a bounded
generalization diagnostic, not an untouched final holdout.
