# Current Offline GEPA

> Authority: current Offline GEPA experiment contract
>
> Last reviewed: 2026-08-16

## Objective

Offline GEPA learns a standalone plan-review guideline that both a human
reviewer and an Agent can apply before implementation to decide whether a
proposed plan should proceed. The experiment does not prescribe the
guideline's format, sections, review actions, or knowledge contents; those are
variables for GEPA to explore. Guideline acceptance is assessed separately by
predictive performance, observable review behavior, transferability, and human
understandability.

The experiment-only Checker prompt must not silently supply software-engineering
knowledge or investigation methods that the deployed guideline omits. Its
fixed responsibility is limited to suppressing reliance on unstated model
knowledge, exposing the available inputs and disposable execution environment,
and enforcing method-independent action/output protocol. General review
knowledge and useful interaction behavior belong in the candidate guideline so
GEPA can optimize them and a developer can read them.

Offline does not assume that agreement with historical execution outcomes is
identical to plan quality. Held-out errors must be analyzed for label
prevalence, Checker bias, and historical Code Agent effects before making that
claim.

The planned external PolyBench evaluation is specified separately in
[`offline-polybench-validation.md`](offline-polybench-validation.md). It first
regenerates plan/outcome evidence with a frozen official Python-199 PCE flow,
then performs Checker-only evaluation of guidelines frozen before any PolyBench
result is viewed. PolyBench never enters GEPA or Reflection.

```text
issue + historical Round 1 plan + base repository + candidate guideline
  -> fixed Checker -> predicted_resolved
  -> configured agreement metric against historical resolved
  -> GEPA Reflection -> complete replacement guideline
```

## Semantic Compatibility

The current standalone-guideline method is not compatible with historical
"strong fixed Checker + optimized approval checklist" runs. A change to the
Checker or Reflection responsibility boundary, primary metric, seed, dataset,
model/search semantics, or shared Agent protocol requires a new run identity;
it must not silently adopt an older candidate tree or score history. Historical
run status and results belong in `project_issues.md` while they support a live
decision, and in `output/README.md` plus `output/catalog.json` thereafter.

## Experimental Requirements

- Candidate text is the complete transferable plan-review guideline, not only
  a list of approval criteria.
- The guideline's review behavior, decision policy, organization, and explicit
  knowledge are optimization variables. The project must not prescribe their
  contents or a fixed section structure in the Checker or Reflection prompt.
- The fixed Checker prompt must not teach repository-inspection strategy or
  plan-quality criteria. It may state the input/tool boundary, suppress use of
  unstated model knowledge, and retain mini-swe action plus final-output
  protocol.
- Checker repository interaction occurs in a disposable benchmark image. It
  may write diagnostic code or tests and make temporary source changes; these
  changes are evidence only and never alter the historical dataset or base
  repository outside the discarded environment.
- Reflection receives rich current-minibatch trajectories and execution-after
  evidence. Its prompt begins with the intended standalone artifact, does not
  prescribe a topic taxonomy, and requires a causal account from current
  guideline through review behavior to the proposed change.
- The current formal optimization run uses `accuracy` as its selected primary
  metric, a default-accept minimal seed, Reflection minibatch eight, and eight
  GEPA proposal iterations. Balanced accuracy, class-explicit
  precision/recall, MCC, pass rate, and the confusion matrix remain required
  diagnostics.
- No action-count reward is added merely to make the Checker run more commands.
  Information-seeking quality is audited from saved trajectories and concrete
  cases before any process metric is considered.

## Guideline Reflection

The Reflection prompt begins with only the target artifact rather than a
detailed description of the Checker. It asks for a complete, self-contained
guideline and states that a separate Checker prompt will not supply omitted
review behavior, decision guidance, or software-engineering knowledge. It does
not enumerate desired review actions, prescribe checklist sections, or declare
that every condition is an absolute approval or rejection gate.

The structured review still covers every minibatch case, but it now records a
causal chain: relevant repository understanding, the current guideline
instruction or omission, the resulting review behavior, the diagnosis, the
expected effect of a proposed guideline change, and risk to correctly handled
cases. The separate `guideline_changes` records connect each textual change to
that causal evidence.

Reflection does not currently mount twelve benchmark repositories. It
understands repository behavior from file contents, commands, and test results
preserved in Checker, Plan, Code, patch, and evaluator evidence. The prompt
states this boundary directly and requires uncertainty rather than an invented
repository fact when the captured evidence is insufficient. Giving Reflection
fresh repository access would be a separate method and infrastructure change,
not an implicit consequence of this prompt revision.

The current formal run uses a Reflection minibatch of eight rather than twelve.
The immediate experimental reason is the stopped 2026-08-06 run: a twelve-case
rich-evidence bundle led Reflection to request about 1.26 million input tokens,
above the model's 1,048,576-token context limit, on all three fresh attempts.
Reducing the minibatch is the smallest method-visible change that lowers this
risk without adding a summarizer, reviewer, or evidence-pruning authority. It
may increase proposal variance and minibatch overfitting, so eight is a trial
setting rather than an established optimum.

## Reflection And Search Boundary

GEPA uses evidence and scores through different channels. For proposal
generation, the parent candidate is evaluated on the current train minibatch
with complete Checker trajectories and historical execution-after evidence;
Reflection reads that rich bundle and proposes replacement text. For search,
the proposal must obtain a strictly higher scalar score than its parent on the
same minibatch before it reaches full validation. Validation then updates the
instance-level Pareto frontier, while aggregate validation metrics identify the
reported best candidate.

The scalar gate does not mean Reflection receives only a label. It means that a
better investigation process has no selection advantage unless it changes the
scored prediction on the sampled cases. Validation cases are held out from
Reflection, so their trajectories may be retained for audit but are not direct
mutation feedback. This separation is part of GEPA's reflective search design.
It also creates a validity risk: a prompt can narrow rich evidence into
superficial edits, while the historical-outcome metric can disagree with actual
pre-implementation plan quality.

## Information Boundary

The Checker receives only the issue, historical Round 1 plan, candidate
guideline, and repository at the base commit. Its fixed prompt exposes the
input and disposable environment plus the method-independent action/output
protocol; `<candidate_rules>` is inserted into the user message and is the only
evolving review guidance.

Checker and Reflection both receive the shared mini-swe-agent system guide,
which positively states the one-fenced-`bash`-action parser contract and shows
one accepted response. After zero or multiple fenced `bash` actions, the
invalid response is not executed. The same Agent receives the detailed
correction from mini-swe-agent 1.17.5's official SWE-bench config—including
the detected action count and one-action example—and must emit a new response.
This is fixed transport guidance rather than part of the candidate guideline.

Final Checker output is validated by worker-side host Python after
mini-swe-agent has accepted the completion action. If that output is empty,
invalid JSON, or violates the Checker output schema, the failed attempt and
complete trajectory are preserved. A configured retry starts a fresh
Agent/container and receives only the previous worker-validator error in
`<retry_feedback>`. It does not receive
the historical label, score, ASI, GEPA acceptance state, or the previous
semantic answer. Infrastructure/provider failures do not enter this feedback.
The feedback changes only output-protocol recovery and is part of the
fingerprinted Checker semantics.

The formal HPC configuration gives one complete Checker worker a 35-minute
Slurm allocation. Slurm owns this wall-time; the HPC worker does not install a
second 30-minute process alarm or reserve five minutes that the Agent cannot
use. SIF preparation, repository-environment construction, Agent work, final
submission, and ordinary cleanup all occur inside the one allocation.
`checker.agent_timeout_seconds` is local-only and is `0` in the formal HPC
config. `checker.timeout` remains the maximum for an individual environment
operation, not a second whole-worker budget.

The worker appends and flushes every Checker system, user, and assistant
message to the attempt-local `checker_trajectory.jsonl`. This is raw evidence,
not a classification decision. If Slurm kills the process before an atomic
worker result can be written, the resumed controller reads the journal and the
recorded Slurm terminal state. This separates the worker's execution duty from
the controller's collection and outcome-policy duty.

Offline HPC task manifests must remain valid across controller slices. Each
`ulhpc-submit` call may execute from a different immutable source snapshot,
while the run directory and task batches are persistent. Worker file locators
stored in Checker and Reflection-repair manifests are therefore relative to
their manifest, not absolute paths through a controller snapshot. Candidate
text remains protected by its SHA-256 and exact content check. A changed
candidate, case payload, repetition identity, prompt/runtime fingerprint, or
output identity still blocks; a changed operational mount prefix does not.

Resolved labels, historical Plan/Code trajectories, patches, evaluator results,
and scores are never Checker inputs. They are available only to Reflection as
post-execution diagnostic evidence. Reflection runs in a lightweight container
with the current evidence bundle mounted at `/evidence`; it does not freely
enter each benchmark repository.

Before a Reflection proposal is returned to GEPA, a deterministic
high-precision check rejects exact current-minibatch instance/repository
identifiers, complete Checker-evidence paths, and code symbols containing `_`
or `::`. A dot alone is not treated as a code-symbol signal because it is also
ordinary sentence punctuation; path placeholders such as `.` and `/` are also
ignored. The check performs no fuzzy or semantic matching and does not add a
score.

When the check finds a match, the original proposal and complete trajectory are
preserved and the same Reflection configuration receives the proposal plus the
exact matches for one generalization repair. Locally this is a second
sequential Agent call. On HPC it is a second fingerprinted Slurm task whose
immutable input references the completed initial proposal and evidence bundle.
A clean repair is returned to GEPA. If that single repair still contains a
match, the proposal fails and GEPA retains its parent; there is no additional
judge or unbounded retry.

The Reflection instance prompt documents the evidence API explicitly.
Reflection must read every case listed in `manifest.json`, read each
`checker_output.json`, inspect the raw Checker and execution-after evidence for
every FP and FN, and use correct cases to test whether a proposed rule would
damage existing correct behavior. Per-instance `plan_trajectory.json`,
`code_trajectory.json`, `generated.patch`, and `evaluator_result.json` remain
available for deeper diagnosis. There is no additional model-facing summary:
the structured case reviews themselves are the auditable minibatch overview.

Before rules can be submitted, Reflection writes a structured analysis with
one diagnosis and an `evidence_used` list for every minibatch case. Every add,
revise, delete, or preserve decision also records its rationale and supporting
case IDs. Each review separately records whether the historical result is
attributable to planning, Code, evaluation/infrastructure, mixed causes, or
uncertainty, and whether it supports a rule change, rule preservation, no
rule inference, or remains uncertain.

Only the complete replacement rules are returned to GEPA and later shown to
the Checker. The outer orchestration attempts to preserve the Agent-written
analysis as `reflection_analysis.json`; missing or invalid analysis is recorded
but does not reject, repair, or otherwise change the proposal. The complete
Agent trajectory remains the raw authority. The analysis is diagnostic
evidence, never part of the candidate rules or Checker input.

## Dataset And Metric

The immutable snapshot is
`output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`:

| Split | Total | Resolved | Unresolved |
|---|---:|---:|---:|
| Train | 384 | 251 | 133 |
| Validation | 98 | 64 | 34 |

The primary metric is selected by config. `accuracy` gives each case a score of
one for agreement and zero otherwise. The supported `balanced_accuracy` option
uses this per-example weight within one split:

```text
correct score = split_size / (2 * historical_class_count)
incorrect score = 0
```

The mean weighted score over a complete split equals balanced accuracy. Both
metrics preserve GEPA's per-example cache and minibatch comparison contract.
The selected primary metric affects Reflection evidence, parent/proposal
minibatch comparison, full-validation aggregate scores, and best-candidate
selection; downstream reports derive their primary score from the same config.
It does not change Checker-visible inputs. With balanced accuracy,
`skip_perfect_score` must remain false because the classes do not share one
per-example perfect score.

### Checker stability diagnostic

The `offline_checker_stability` mode evaluates one fixed guideline on the full
validation split three independent times. Runs using the same immutable
snapshot use the same fixed 98 validation cases; validation is not resampled
between runs. A repetition tag changes only the evaluation/cache identity; all
Checker inputs, prompts, model settings, and repository environments remain the
same. The mode does not construct a Reflection proposer and does not call
`gepa.optimize`.

For each case, `correct_count` is the number of predictions equal to the
historical resolved label. `3/3` and `0/3` are stable decisions; `2/3` and
`1/3` are decision flips. Prediction direction is reported separately through
the accept-count distribution. Timeout or operationally incomplete triples are
excluded from all four decision bins and reported as incomplete, so execution
failure cannot masquerade as Checker uncertainty. Complete raw trajectories
remain under the fingerprinted `hpc_tasks/checker/` batches.

The completed 2026-08-07 diagnostic set `hpc.max_task_attempts=1`. Its three
configured repetitions are the only repeated Checker sessions. A semantic
timeout is reported as an incomplete repetition without a fresh retry.
Exhausted worker or output-contract failures are also incomplete and keep their
raw category.
Host validation, identity, or data-integrity failures retain the existing
blocking boundary; no failure is converted into a prediction.

### Repeated-minibatch evaluation (3 x 3)

The repetition layer is implemented but remains opt-in. Set
`search.reflection_minibatch_size: 3` and
`search.train_case_repetitions: 3` under a new run identity to activate the
3 x 3 method. The default repetition count is one, preserving the ordinary
single-evaluation flow. Its purpose is to expose Checker decision instability
to Reflection without repeating the 98-case validation or increasing the
number of distinct training cases that Reflection must understand.

1. The existing sampler selects three distinct training cases. It remains the
   sole authority for case selection.
2. A separate repetition layer expands each selected case into three
   independently identified Checker evaluations, for nine Checker tasks. Each
   repetition has a stable repetition number in its cache/task identity, while
   the Checker prompt and Checker Agent are not told that the case is repeated.
3. On HPC, every repetition is one independent Slurm task. All nine tasks are
   submitted without a project-side concurrency cap; Slurm controls scheduling.
4. Each repetition reuses the existing Checker failure policy and may use up to
   three fresh attempts. Attempts are an automation mechanism, not experimental
   observations: all raw attempt artifacts remain auditable, but only the first
   successful terminal result, or the final terminal timeout after exhaustion,
   is exposed outside that repetition. Operational or integrity exhaustion
   retains the existing blocking behavior.
5. The three binary correctness scores for one base case are averaged, so its
   GEPA score is one of `0`, `1/3`, `2/3`, or `1`. Dividing by three preserves
   the existing `[0, 1]` metric scale and makes the case weight independent of
   the repetition count.
6. GEPA and its sampler still see three logical cases and three logical scores.
   Reflection receives one grouped record per base case containing the three
   terminal Checker results and trajectories. The evidence JSON records the
   repetition count and index. The exact Reflection instruction for using that
   disagreement remains a pending prompt-design decision.
7. Full validation is unchanged: each of the fixed 98 validation cases is
   evaluated once. Repetition therefore changes proposal evidence and the
   parent/proposal minibatch gate, not validation measurement or candidate
   selection on the held-out split.

The accounting must distinguish logical GEPA metric calls from physical Agent
sessions. With minibatch `B=3`, one parent or proposal minibatch evaluation is
three logical calls but nine initial Checker sessions. Attempts do not create
additional logical scores; they only increase physical sessions after a failed
session, to at most 27 for that evaluation. For eight proposals and validation
size 98, the conservative GEPA-call projection remains
`98 + 8 * (3 + 3 + 98) = 930`, while the initial physical-session projection is
`98 + 8 * (9 + 9 + 98) = 1026` before retries.

This design depends on a strict attempt-evidence boundary. A semantic timeout
is scored as before, but Reflection sees only the last partial trajectory and
no attempt count, retry feedback, or earlier attempt trajectories. Complete
attempt evidence remains under the local `checker_trajectories/` or HPC
per-attempt task directories. This prevents orchestration retries from changing
the research evidence merely because the automation policy was configured with
more attempts.

Implementation boundaries are explicit: the sampler returns logical cases;
the Adapter expands only train batches, the local or HPC Checker executor runs
the physical calls, and the Adapter restores one score and one grouped evidence
record per logical case before returning to GEPA. Repetition identity is part
of the HPC task manifest, output validation and evaluation fingerprint, but is
excluded from `checker_payload`. Validation cases are never expanded. With the
configuration disabled, output and Reflection evidence retain the pre-existing
single-call schema.

## Search, Stopping, And Resume

`search.max_iterations` is the primary stop condition and is an absolute
cumulative proposal target across resume. `search.max_metric_calls` is a
fail-safe above the documented worst-case projection, not the experiment
target. For validation size `V`, minibatch size `B`, and proposal target `I`,
the conservative projection is `V + I * (B + B + V)`: seed validation plus
parent minibatch, proposal minibatch, and possible proposal validation on every
iteration. Exhausting the ceiling before the proposal target is an anomaly to
investigate rather than a reason to expand the budget silently.

The shared iteration-target supervisor is the single launch and resume entry
point. A local `tmux + caffeinate` service polls on its configured cadence and
submits short controller allocations. A controller submits fingerprinted
Checker arrays or one Reflection Agent task and then yields; the next
controller replays the same GEPA call and collects the atomic output. Durable
progress is the number of completed proposals.

The ordinary run manifest permits a metric-call ceiling increase but rejects
changes to data, source, prompts, model/search semantics, seed, or iteration
target. If a completed run's cumulative target is extended, the supervisor
verifies its checkpoint, preserves the original manifest and terminal reports,
records the old/new targets and hashes, and reopens the same GEPA state. The
new target is always cumulative; operators do not calculate or pass an
additional-iteration value.

The current 3 x 3 staged run uses this contract explicitly. The completed
`offline-plan-guideline-hpc-accuracy-b3x3-2it-smoke-postfix-20260816` run is
the frozen two-proposal checkpoint. Its extension config sets the cumulative
target to eight, the conservative logical-call projection to `930`, and the
fail-safe ceiling to `1200`, while preserving the same run directory, dataset,
seed guideline, prompts, models, accuracy metric, sampler, and 3 x 3 repetition
semantics. A new supervisor identity points at that same persistent run and
requires a clean worktree; the old two-iteration supervisor state is not
retargeted. Reports must describe this as a staged 2-to-8 continuation because
the retained run identity contains `2it-smoke`, even if the final checkpoint
reaches eight proposals.

## Shared Mini-swe Transport Contract

Every project DefaultAgent receives the same method-independent parser guide.
It describes the whole-response input, the exact lower-case fenced-`bash`
matching rule, the captured action list, and the execute-or-reject result. A
valid response contains exactly one executable fenced `bash` block. Zero or
multiple matches, including multiple blocks that are individually valid, are
rejected without executing any of them. The Agent must not write or simulate
later-step blocks before receiving the actual observation for its current
block.

On rejection, the retained conversation receives a detailed correction that
states the observed match count, confirms that nothing ran, restates the parser
contract, and asks for one corrected block. The original response and complete
trajectory remain raw evidence. This contract controls only Agent transport;
it does not prescribe Checker investigation, Reflection reasoning, or
guideline contents.

## HPC Task And Resume Boundary

The shared `src/optimization/hpc/` package owns only Slurm configuration,
status queries, deterministic submission reconciliation, grace windows, and
the atomic task lifecycle. Offline code owns Checker/Reflection inputs, output
validation, and GEPA scoring. Online retains its method-specific phase journal
while importing the same Slurm configuration and command boundary.

One Checker array element runs one complete Checker Agent session for one
case. Historical labels and ASI are absent from its task manifest; scoring
remains in the controller after output collection. Reflection is a separate
single-element initial task whose immutable input contains the current
candidate and all current-minibatch evidence. If deterministic contamination
checking requests repair, the completed initial output becomes immutable input
to a second single-element repair task. Initial and repair Agent conversations
never share a process or writable workspace.

Only a `status=completed` output with matching fingerprint, instance identity,
and schema is reusable. A killed or invalid attempt is never resumed inside an
Agent conversation. If the configured retry policy permits another attempt,
the whole Checker, initial Reflection, or repair task starts again in a new
attempt directory. For a Checker output-contract failure, only the archived
validator error is copied into the new attempt's immutable retry context; the
previous Agent process and writable repository state are not resumed.
Slurm terminal state and temporarily missing accounting records wait through
configured output/missing grace periods before retry or failure. GEPA state,
not Slurm task completion, remains the authority for whether a result affected
the search.

If all three fresh Checker attempts end in Slurm `TIMEOUT` and every attempt's
durable journal contains at least one assistant message, the controller returns
`predicted_resolved=null` and assigns that case score zero. This proves the
Checker reached Agent reasoning rather than timing out before the experimental
task began. The final attempt's partial trajectory enters Reflection evidence;
attempt count, retry feedback, and earlier attempt trajectories remain only in
raw audit artifacts. A missing/empty journal, no assistant response, mixed
failure kind, output-contract exhaustion, provider failure, non-timeout Slurm
state, or identity/schema conflict still blocks rather than being attributed to
the guideline.

If initial Reflection or contamination repair exhausts all three attempts, its
task journal still enters the durable `EXHAUSTED` terminal state, preserving
the exact operational failure. The proposer then raises an ordinary proposal
exception. GEPA treats that proposal as unsuccessful and starts its next
proposal iteration, which samples a new minibatch; it does not create a
candidate or score the failed proposal. The failed proposal attempt does count
toward GEPA's configured iteration/proposal-attempt limit. Identity, schema,
host-validation, and other integrity failures still use the dedicated blocking
exception and stop the run.

Candidate reports preserve timeout predictions as JSON `null`, report timeout
count/rate separately, and recompute the GEPA validation score from the stored
per-case scores. Ordinary confusion-matrix metrics are calculated only over
completed binary Checker predictions and are labeled with that scope; timeout
cases are never silently coerced to `false`.

## Local/HPC Configuration Switch

The algorithm has one configuration model. `execution.backend` selects only
the execution transport:

| Mode | Required config | Behavior |
|---|---|---|
| Local Docker | `execution.backend: local`, `container.runtime: docker` | Checker calls use local thread parallelism unless `checker.agent_timeout_seconds` is enabled; the POSIX soft deadline requires `search.parallel=1`. Reflection and optional repair run synchronously |
| HPC Slurm | `execution.backend: hpc_slurm`, `container.runtime: apptainer` | Checker, initial Reflection, and optional repair are independent Slurm tasks; Slurm wall-time plus controller evidence classification replaces the local soft deadline |
| Local Apptainer | `execution.backend: local`, `container.runtime: apptainer` | Synchronous execution on a host with Apptainer and the SIF cache; an enabled Checker soft deadline requires `search.parallel=1` |

`hpc.*` remains config-validated but is not used to execute local Agent calls.
Switching backend or container runtime changes experimental semantics and
therefore requires a new `paths.run_dir`; the run manifest rejects adopting an
old identity.

Offline HPC rejects `hpc.max_running_array_tasks` and the legacy
`hpc.array_concurrency` alias. Checker scripts contain the full array index set
without a `%N` suffix. Retry arrays contain only failed task indices, also
without a project-level running limit. Slurm is the sole scheduling authority.

## Evidence And State

The run directory preserves:

- `run_manifest.json` for immutable logical-run identity;
- `gepa_state.bin` and `gepa_resume_state.json` for official GEPA plus sampler,
  selector, Reflection, and accepted-candidate resume state;
- `iteration_progress.json` and `controller_status.json` for the shared
  supervisor contract; the former stores a completed-proposal count and the
  latter supplies explicit controller completion/failure rather than inferring
  it from result-file existence;
- `hpc_tasks/checker/<fingerprint>/` and
  `hpc_tasks/reflection/<fingerprint>/` for immutable inputs, per-attempt
  evidence, task state, atomic initial output, and nested fingerprinted repair
  task when required;
- `progress.json`, `audit_events.jsonl`, and `evaluations.jsonl`;
- every Checker call's complete trajectory;
- attempt-local `checker_trajectory.jsonl` incremental journals, which preserve
  partial Checker evidence across a hard Slurm stop;
- per-attempt `retry_feedback.json` when a fresh Checker receives a previous
  output-validator error;
- worker failure records with the exact exception type plus deterministic
  `failure_stage` and `failure_category`;
- `host_validation_failure.json` when a worker-declared completed output fails
  controller-side envelope, identity, fingerprint, or schema validation; the
  raw output is preserved and the batch blocks rather than retrying an Agent;
- per-attempt `slurm_status.json` for terminal or missing-output observations,
  plus Slurm stdout/stderr under the fingerprinted batch's `slurm_logs/`;
- `reflection_inputs/*/reflection_trajectory.json` inside per-proposal evidence
  bundles;
- `reflection_analysis.json` when the Agent produced parseable JSON diagnostic
  evidence, or `reflection_analysis_invalid.txt` when malformed;
- local `reflection_inputs/*/reflection_repair_trajectory.json`, or HPC repair
  attempt evidence, when a proposal requires contamination repair;
- candidate guidelines, validation metrics, reports, errors, token use, and cost;
- `best_guideline.txt` and `guideline_sha256` in new derived reports. The
  shared third-party GEPA state still uses the internal component key `rules`;
  this compatibility key is not the name or intended structure of the Offline
  artifact.

Operational Checker exhaustion stops the optimization. It must not be converted
into an unresolved prediction or silently included in candidate comparison.
Operational Reflection exhaustion likewise blocks instead of being converted
into a GEPA no-proposal iteration. `BLOCKED` and `EXHAUSTED` task journals are
terminal and are replayed as the same failure rather than inferred again from a
stale `SUBMITTED` state.

## Controller Entry Point

Only run after explicit authorization and after confirming the new run identity
does not already contain incompatible state:

```bash
conda run -n mini-swe python -m src.optimization \
  --config configs/gepa_verified_rules.yaml
```

The versioned unattended launch identity is
`configs/offline_gepa_supervisor.yaml`. Starting it is an external HPC action
and requires explicit authorization.
