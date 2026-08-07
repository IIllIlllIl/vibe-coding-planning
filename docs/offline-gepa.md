# Current Offline GEPA

> Authority: current Offline GEPA experiment contract
>
> Last reviewed: 2026-08-05

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
- The next optimization run uses `accuracy` as its selected primary metric.
  The current launch configuration is instead a Checker-only stability
  diagnostic: it repeats the complete validation split three times, preserves
  every trajectory, and invokes neither GEPA proposal nor Reflection.
  Balanced accuracy, class-explicit precision/recall, MCC, pass rate, and the
  confusion matrix remain required diagnostics for optimization runs.
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

The next draft uses a Reflection minibatch of eight rather than twelve. The
immediate experimental reason is the stopped 2026-08-06 run: a twelve-case
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

The formal HPC configuration gives one complete Checker Agent session a
30-minute soft deadline inside a 35-minute Slurm allocation. Reaching the soft
deadline interrupts the Agent before Slurm kills the worker, leaving five
minutes for container cleanup plus atomic failure and partial-trajectory
writes. This session deadline is `checker.agent_timeout_seconds`; the older
`checker.timeout` remains the per-command/SIF-preparation timeout and does not
bound a complete Agent conversation.

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

The current diagnostic sets `hpc.max_task_attempts=1`. Its three configured
repetitions are the only repeated Checker sessions. A semantic timeout is
reported as an incomplete repetition without a fresh retry. Exhausted worker
or output-contract failures are also incomplete and keep their raw category.
Host validation, identity, or data-integrity failures retain the existing
blocking boundary; no failure is converted into a prediction.

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

If all three fresh Checker attempts end with an explicitly written
`checker_agent_timeout`, the host reconstructs that attempt evidence, returns
`predicted_resolved=null`, and assigns that case score zero. The timeout and
all available partial trajectories enter Reflection evidence. This treats
open-ended review caused by a candidate guideline as candidate failure without
inventing a binary prediction. A hard Slurm `TIMEOUT`, missing output, mixed
failure kinds, output-contract exhaustion, provider failure, or infrastructure
failure still blocks; these cannot safely be attributed to the guideline.

If initial Reflection or contamination repair exhausts all three attempts, its
task journal enters the durable `EXHAUSTED` terminal state and the Offline run
blocks. Reflection uses a dedicated controller exception that passes through
GEPA's ordinary proposal-exception handler, so exhaustion cannot become a
normal no-proposal iteration or consume the configured iteration target.

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
| HPC Slurm | `execution.backend: hpc_slurm`, `container.runtime: apptainer` | Checker, initial Reflection, and optional repair are independent Slurm tasks |
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
