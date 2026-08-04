# Current Offline GEPA

> Authority: current Offline GEPA experiment contract
>
> Last reviewed: 2026-08-04

## Objective

Offline GEPA learns a standalone plan-review guideline that both a human
reviewer and an Agent can apply before implementation. The guideline is not
only an approval checklist. It must guide how to interact with the repository,
obtain and process relevant evidence, recognize when important information is
still missing, and then decide whether the proposed plan should proceed.

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

## Experiment Status And Semantic Versions

The formal HPC run
`offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731` used the earlier
"strong fixed Checker + optimized approval checklist" boundary. It reached 14
durable proposals after its observed 8it checkpoint was extended toward 20,
then stopped during the attempted fifteenth proposal because the provider
reported insufficient balance. Its candidate tree and raw evidence remain
valid for analysis of that frozen semantic version, but it must not be resumed
under the redesigned prompts or metric.

Analysis of that run found that the fixed Checker already supplied most
repository-interaction behavior, while candidate changes mainly shifted the
approval threshold. This conflicts with the standalone-guideline objective.
The next experiment therefore requires a new run identity and a prompt/config
revision. The current runtime config now stages the agreed minimal Checker,
standalone-guideline Reflection target, causal case analysis, and `accuracy`
metric. Its run identity and stopping budget still describe the frozen
experiment, so it is intentionally not launch-ready. The copied run manifest
and raw evidence, rather than the evolving current config, are the authority
for reproducing or analyzing the frozen 14it run.

## Next Experiment Requirements

- Candidate text is the complete transferable plan-review guideline, not only
  a list of approval criteria.
- GEPA may optimize investigation behavior, evidence-processing guidance,
  stopping/uncertainty behavior, decision guidance, organization, and necessary
  explicit software-engineering knowledge. The project must not prescribe a
  fixed section structure for those contents.
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
- The next run uses `accuracy` as the GEPA primary metric. Balanced accuracy,
  precision/recall, MCC, pass rate, and the confusion matrix remain required
  diagnostic reports.
- No action-count reward is added merely to make the Checker run more commands.
  Information-seeking quality is audited from saved trajectories and concrete
  cases before any process metric is considered.

## Staged Guideline Reflection

The next Reflection prompt begins with the target artifact rather than a
detailed description of the Checker. It asks for a complete standalone
guideline whose investigation behavior, evidence processing, decision method,
organization, conditions, and exceptions are all available for GEPA to
optimize. It does not prescribe checklist sections or declare that every
condition is an absolute approval or rejection gate.

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
mutation feedback. This separation is part of GEPA's reflective search design;
the frozen Offline problem is that its prompt narrowed rich evidence into
checklist edits and its historical-outcome metric could disagree with actual
pre-implementation plan quality.

The frozen 14it evidence contains 14 saved structured Reflection analyses.
Every one records reviews for all 12/12 manifest cases and declares all 12
Checker outputs as evidence. Deeper Plan/Code/patch/evaluator reads vary by
classification and need. This proves nominal minibatch coverage, not semantic
understanding; review quality must still be checked against raw trajectories.

The frozen run did learn a small amount under its configured objective: seven
of 14 proposals passed the same-minibatch gate, but only two improved their
parent on aggregate validation and the best balanced accuracy rose from
`0.706342` to `0.731158`. The other five gated proposals generalized worse.
This makes inaccurate historical labels a validity risk, but not a sufficient
explanation for the weak optimization. The immediate optimization problem is
that evidence-driven proposals frequently gained on 12 adaptive examples and
failed to transfer even to the existing 98-case validation set.

## Frozen 20260731 Information Boundary

The frozen-run Checker receives only the issue, historical Round 1 plan,
candidate rules, and repository at the base commit. Its fixed system prompt
supplies execution permissions and the output protocol; `<candidate_rules>` is
inserted into the user message and is the only evolving approval standard.

Its fixed prompt requires the Checker to inspect the repository and verify
important plan claims. It may search/read files, inspect existing tests, run
existing tests against the unmodified repository, and use temporary diagnostic
scripts outside the repository. It may not modify repository source/tests,
implement the proposed solution, or judge the plan from a patch it creates.

Checker and Reflection both use the shared mini-swe-agent action protocol.
After zero or multiple fenced `bash` actions, the invalid response is not
executed. The same Agent receives the detailed correction from
mini-swe-agent 1.17.5's official SWE-bench config—including the detected action
count and one-action example—and must emit a new response. This is fixed
execution feedback rather than part of the candidate rules or experiment
prompt.

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

## Frozen 20260731 Dataset And Metric

The immutable snapshot is
`output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`:

| Split | Total | Resolved | Unresolved |
|---|---:|---:|---:|
| Train | 384 | 251 | 133 |
| Validation | 98 | 64 | 34 |

The frozen run's primary metric is balanced accuracy. For an example in one split:

```text
correct score = split_size / (2 * historical_class_count)
incorrect score = 0
```

The mean weighted score over a complete split equals balanced accuracy. The
additive score also preserves GEPA's per-example cache and minibatch comparison
contract. It affects Reflection evidence, parent/proposal minibatch comparison,
full-validation aggregate scores, and best-candidate selection. It does not
change Checker-visible inputs. `skip_perfect_score` must remain false because
the two classes do not share one per-example perfect score.

## Frozen 20260731 Search And Stopping

The copied run manifest records these frozen settings. The evolving current
config at [`../configs/gepa_verified_rules.yaml`](../configs/gepa_verified_rules.yaml)
must not be used as their reproduction authority:

- full 384/98 snapshot;
- minimal seed from `configs/gepa_initial_rules_minimal.md`;
- epoch-shuffled train minibatches of 12;
- instance-level validation Pareto selection;
- twenty cumulative candidate-proposal iterations, extended after observing the
  completed 8it checkpoint;
- balanced accuracy;
- one independent Apptainer Slurm array element per Agent, with the complete
  Checker batch submitted at once and no project-level concurrency throttle;
- three total attempts for each failed Checker, Reflection, or repair task;
- local-only GEPA adapter parallelism 1; HPC task scheduling belongs only to
  Slurm.

The minibatch remains 12 so each proposal must account for a meaningful
cross-case sample rather than a very small, noisy batch. The current HPC run
started fresh rather than inheriting the local 8it baseline. After its first
eight iterations completed, its stopping target was explicitly extended to 20
while retaining the same HPC candidate tree, scores, RNG, and sampler. Because
this extension was chosen after observing the 8it outcome, analyses must report
the 8it checkpoint separately and must not describe 20it as a preregistered
target. Raw trajectories need not all be read in full: every case receives a
structured review, every error receives a deeper diagnosis, and correct cases
provide regression checks.

`max_iterations=20` is the primary stop condition. GEPA's official saved state
is cumulative, so the extension continues from 8 toward 20 total proposals
rather than adding 20. The total worst-case evaluation count is
`98 + 20 * (12 + 12 + 98) = 2538`. `max_metric_calls=3000` is a fail-safe,
not the experiment target. Exhausting it before twenty proposals is
an anomaly to investigate rather than a reason to expand the budget silently.

The current run uses the shared iteration-target supervisor contract. A local
`tmux + caffeinate` service polls every 10 minutes and submits short controller
allocations. A controller
submits fingerprinted Checker arrays or one Reflection Agent task and then
yields; the next controller replays the same GEPA call and collects the atomic
output. Durable supervisor progress is the number of completed proposals:
GEPA's zero-based final `state.i` is published as `state.i + 1`, so a terminal
callback cannot overwrite a saved count of two with index one. On successful
completion, `controller_status.json` supplies the terminal run status when the
GEPA `result.json` has no top-level status field.

## Current 20260804 Behavior Smoke

The current config uses a new run identity and does not resume the frozen
20260731 candidate tree. It keeps the full 384/98 snapshot and minibatch 12,
but stops after six proposals and uses accuracy as the primary GEPA metric.
Its worst-case evaluation projection is
`98 + 6 * (12 + 12 + 98) = 830`; `max_metric_calls=1000` remains a fail-safe.
The purpose is to observe whether the revised Checker and Reflection prompts
produce repository-grounded, causally motivated, transferable guidelines.
Six proposals are not enough to claim optimization effectiveness or held-out
generalization.

The ordinary run manifest permits a metric-call ceiling increase but rejects
changes to data, source, prompts, model/search semantics, seed, or iteration
target. Supervisor is the single resume entrypoint. For Offline runs it reads
the cumulative target directly from `search.max_iterations`. If a run completed
at a smaller stored target, supervisor verifies that checkpoint, saves its
terminal reports and original manifest, records the old/new target and hashes,
and reopens the run before invoking the ordinary resume path. It changes no
GEPA search state. Thus a target of 20 always means 20 cumulative proposals;
operators do not calculate or pass an additional-iteration value.

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

If a Checker, initial Reflection, or contamination-repair task exhausts all
three attempts, its task journal enters the durable `EXHAUSTED` terminal state
and the Offline run blocks. Reflection uses a dedicated controller exception
that passes through GEPA's ordinary proposal-exception handler, so exhaustion
cannot become a normal no-proposal iteration or consume the configured
iteration target. No prediction, score, or candidate guideline is fabricated.

## Local/HPC Configuration Switch

The algorithm has one configuration model. `execution.backend` selects only
the execution transport:

| Mode | Required config | Behavior |
|---|---|---|
| Local Docker | `execution.backend: local`, `container.runtime: docker` | Checker calls use local thread parallelism; Reflection and optional repair run synchronously |
| HPC Slurm | `execution.backend: hpc_slurm`, `container.runtime: apptainer` | Checker, initial Reflection, and optional repair are independent Slurm tasks |
| Local Apptainer | `execution.backend: local`, `container.runtime: apptainer` | Synchronous execution on a host with Apptainer and the SIF cache |

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
