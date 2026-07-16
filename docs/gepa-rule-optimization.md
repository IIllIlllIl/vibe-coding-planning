# Online GEPA Planning Rule Optimization

> Authority: current optimization and evidence semantics
>
> Scope: candidate planning rules, rollout evidence, Reflection, policy v2
>
> Last reviewed: 2026-07-15
>
> Supersedes: `archive/mixed-design/gepa-rule-optimization-mixed-20260715.md`

## 1. Optimization Objective

GEPA optimizes one text component, `rules`. A metric call executes the actual
Plan-Code-Evaluator workflow, so a candidate is judged by current behavior
rather than an offline Checker prediction.

Formal dataset snapshot:

```text
output/SWE-bench_Verified/verified-round1-gepa-datasets/
  20260614_482_fdc056ae85df/
```

It contains 384 train and 98 validation instances. “Small experiment” means a
small number of GEPA iterations, not a reduced formal dataset.

## 2. Visibility Contract

| Phase | May read | Must not read |
|---|---|---|
| Plan | issue, repository, candidate rules | historical outcomes and future phases |
| Code | issue, generated plan, clean repository | candidate rules, historical evidence |
| Evaluator | patch, clean base, official test metadata | rules and Agent trajectories |
| Instance reviewer | issue, base repo, rules, one rollout's evidence | gold patch, validation data, network |

The Code Agent may write and run diagnostic tests inside its isolated phase
workspace. It owns the staged patch returned at formal submission. Online GEPA
does not apply test-path heuristics or silently rewrite that patch: any non-empty
formal submission is transferred byte-for-byte to the clean evaluator. Apply,
test, or submission-selection mistakes are Code evidence and normally score
unresolved; only transfer or clean-environment integrity failures invalidate the
metric call.
| Synthesis | current rules, ordered reviews, targeted current evidence | archived/offline labels as current evidence |

This boundary makes rules causal only through the generated plan.

## 3. Rollout Evidence

A completed trace can contain issue description, repository identity/base
commit, generated plan, Plan trajectory, Code trajectory, patch, evaluator
result, resolved/score, and structured attribution. Failed
partial trajectories remain in attempt diagnostics and do not enter formal
Reflection evidence.

For `capture_traces=true`, each rollout worker adds a repo-grounded reviewer
phase in the matching benchmark SIF. Its structured `instance_review.json`,
redacted trajectory, and identity-bound checkpoint are persisted before the
worker returns. Candidate-comparison and full-validation calls do not run
reviewers. Synthesis reads all instance reviews and writes both
`reflection_analysis.json` and the complete candidate checklist.

Reviewers use question-driven repository interaction rather than a ceremonial
filesystem check. After evidence triage they inspect the untouched base and may
run a focused reproduction/test, write a disposable diagnostic script or test,
apply the generated patch, or make a small counterfactual edit. Their structured
output records attribution questions, repository actions, experiments,
confidence, and uncertainty. Experiments are optional when static evidence is
decisive. Reviewer edits never cross into Code or Evaluator; only the trajectory
and structured review persist.

Each synthesis proposal persists a redacted `reflection_trajectory.json`
beside its evidence bundle. It records the complete mini-swe-agent transcript,
proposal mode, candidate identity, instance IDs, and completion or failure
status. Credential-shaped fields and sensitive environment values are redacted
before the atomic write, and the completion audit event records the path. This
artifact is required to compare agentic evidence exploration with a single
Reflection LLM call.

After a successful Reflection call, the complete proposal is atomically stored
under `reflection_proposals/<fingerprint>.json` before it is returned to GEPA.
The fingerprint covers the parent rules, ordered evidence content, component,
model, temperature, and Reflection prompts. Replaying that exact call returns
the persisted proposal and does not invoke the Agent again. This is an
uncommitted proposal checkpoint only: `gepa_state.bin` remains authoritative
for candidate acceptance and full-validation state.

Reflection should distinguish:

- planning omission or incorrect instruction;
- Code Agent deviation or non-convergence;
- evaluator unresolved after official tests;
- infrastructure-invalid execution.

Rules should become more actionable and concise, not merely longer or more
generic. A fixed Code budget intentionally rewards plans that help execution
converge, but candidate comparisons must still report Code failure rates so
Agent randomness is not mistaken for planning quality.

## 4. Outcome Policy V3

`outcome_status=scored` is the only state accepted into GEPA scores.

- evaluator resolved -> 1;
- evaluator unresolved or official test timeout -> 0;
- final Plan/Code Agent contract failure after all attempts -> 0;
- Slurm-confirmed worker timeout -> selectively retry, then 0 with timeout
  attribution only after the final configured attempt;
- repository/SIF/OOM/checkpoint identity/evaluator harness/output-integrity/
  cleanup failure -> invalid and blocking.

The Code phase has a controlled 2400-second deadline. The worker writes
`code_phase_deadline_exceeded` before Slurm termination. Three total attempts
are allowed. A structured Agent timeout becomes score 0 after those attempts.
A Slurm-confirmed `TIMEOUT` without worker output retries only that index and
becomes score 0 after `hpc.max_task_attempts`; missing output with ambiguous
state stays invalid.

Policy version, Code deadline, retry count, prompts, models, evaluator config,
dataset identity, container config, and optimization source hash participate in
the rollout semantic hash/evaluation fingerprint.

## 5. HPC Batch Resume

Every metric batch stores an immutable evaluation fingerprint and a journal:

```text
PREPARED -> SUBMITTING -> SUBMITTED -> OUTPUTS_READY -> COMPLETE
```

- `SUBMITTED`: query the original job; reuse valid outputs and retry only failed
  or terminal-missing indices.
- `SUBMITTING`: reconcile by deterministic job name before any replacement.
- `OUTPUTS_READY`: all worker files exist, but collection has not completed.
- `COMPLETE`: outputs were validated and returned to GEPA; GEPA state may still
  be uncommitted.
- Legacy batches without identity/journal are not automatically adopted.

A completed Slurm array may remain `SUBMITTED` when GEPA does not replay that
exact metric-call fingerprint after restart. Its files remain reusable, but it
must not be called `COMPLETE` or counted as committed evidence. Reusing an
already `COMPLETE` batch emits a reuse event rather than another completion.

Pending time does not count as worker execution timeout. Running tasks may use
their allocation plus configured output grace. A confirmed `TIMEOUT` is
selectively retried and becomes unresolved only after the attempt limit; other
terminal tasks without output are also selectively retried. An
unknown Slurm task is treated as lost only after its grace and is never inferred
to be an Agent timeout.

Formal HPC Plan and Code YAML omits mini-swe step, cost, and model-attempt
fields. The Online loader disables the retired step/cost mechanisms internally,
and the Plan prompt contains no separate step budget. Per-command timeout,
evaluator timeout, worker Slurm walltime, and the shared HPC attempt count remain
the visible operational boundaries.

## 6. Phase Resume

Worker checkpoints are atomic and identity-bound:

```text
Plan failed       -> retry Plan
Plan succeeded    -> retry clean Code
Code succeeded    -> retry clean Evaluator
Evaluator success -> emit completed output
```

Code retry never continues a half-written repository. It reuses only the
successful Plan artifact and starts a new clean Code workspace. This is a
deliberate correctness tradeoff: incomplete exploration time is lost, but
formal evidence is not contaminated by partial state.

## 7. Controller And Iteration Semantics

`execution.controller_yield_after_submit=true` makes a controller exit normally
after a new or retry array is durably journaled. Audit records
`online_hpc_batch_yielded`; it must not record a batch failure or errors entry.

`online_iteration_progress.json` advances only after official GEPA state save.
Reflection or validation work inside an uncommitted iteration does not count.
The local supervisor targets additional durable iterations, not controller runs
or elapsed walltime.

## 8. Active Configuration

The formal configuration is `configs/gepa_online_planning_hpc.yaml`:

- full 384/98 snapshot;
- `max_metric_calls=1000`;
- reflection minibatch 3 (retained pending collaborator discussion of 6-12);
- up to 150 independent worker elements running concurrently;
- worker `1 CPU / 4G / 55min`;
- Code budget 40 minutes;
- three total attempts for structured Agent failures and non-timeout worker
  failures;
- controller slices 2 hours under the external supervisor.

Outcome policy v3 selectively retries a Slurm-confirmed worker `TIMEOUT`; only
the final exhausted attempt becomes scored unresolved. The controller preserves
any completed Plan/Code checkpoint in Reflection evidence and marks
`terminal_reason=slurm_timeout`. OOM, node,
repository, evaluator-harness, corrupt-output, and ambiguous Slurm failures stay
invalid. Reflection failures are not instance outcomes; they leave the GEPA
proposal uncommitted and are retried by a later controller slice. Successful
Reflection output is reused from its proposal checkpoint if the controller
exits before GEPA commits or rejects it.

`max_running_array_tasks=150` is a Slurm array throttle, not a request for 150
CPUs in one allocation.

## 9. Validity Checklist

Before drawing rule-quality conclusions, verify:

1. no duplicate controller or worker array;
2. no invalid result entered GEPA cache;
3. resumed outputs match candidate/instance/fingerprint;
4. structured Agent failures and retries are reported by candidate;
5. evaluator results came from official tests;
6. iteration count matches official saved state;
7. candidate acceptance matches recorded scores;
8. comparisons use one outcome-policy version.

Historical offline Checker design, pilots, and run timelines are preserved in
the superseded archive document, not in this authority.

## 10. Repo-Grounded Two-Stage Reflection

The active design uses one instance reviewer per trace rollout, inside that
instance's benchmark SIF, followed by one evidence-only synthesis Agent. The
reviewers run across the existing rollout array rather than serially in the
controller. Validation instances, gold patches, persistent repository
mutations, and network access remain forbidden.

Plan, Code, and Evaluator checkpoints make reviewer retry inexpensive. A worker
walltime after evaluator completion retries only the unfinished reviewer; after
the final attempt the evaluator score is preserved and the missing review is
marked uncertain. Synthesis may produce an unchanged checklist when no
defensible deploy-time planning lesson exists. The two-stage prompt and
reviewer semantics require the dedicated formal run identity dated 2026-07-16.
