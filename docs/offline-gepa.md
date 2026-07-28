# Current Offline GEPA

> Authority: current Offline GEPA experiment contract
>
> Last reviewed: 2026-07-28

## Objective

Offline GEPA learns a concise plan-approval standard that both a human reviewer
and an Agent can apply before implementation. It does not assume that agreement
with historical execution outcomes is identical to plan quality; held-out
errors must be analyzed for label prevalence, Checker bias, and historical Code
Agent effects before making that claim.

```text
issue + historical Round 1 plan + base repository + candidate rules
  -> fixed Checker -> predicted_resolved
  -> class-weighted agreement with historical resolved
  -> GEPA Reflection -> complete replacement rules
```

## Information Boundary

The Checker receives only the issue, historical Round 1 plan, candidate rules,
and repository at the base commit. Its fixed system prompt supplies execution
permissions and the output protocol; `<candidate_rules>` is inserted into the
user message and is the only evolving approval standard.

The Checker must inspect the repository and verify important plan claims. It
may search/read files, inspect existing tests, run existing tests against the
unmodified repository, and use temporary diagnostic scripts outside the
repository. It may not modify repository source/tests, implement the proposed
solution, or judge the plan from a patch it creates.

Checker and Reflection both use the shared mini-swe-agent action protocol.
After zero or multiple fenced `bash` actions, the invalid response is not
executed. The same Agent receives the detailed correction from
mini-swe-agent 1.17.5's official SWE-bench config—including the detected action
count and one-action example—and must emit a new response. This is fixed
execution feedback rather than part of the candidate rules or experiment
prompt.

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

The primary metric is balanced accuracy. For an example in one split:

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

## Search And Stopping

The current config is [`../configs/gepa_verified_rules.yaml`](../configs/gepa_verified_rules.yaml):

- full 384/98 snapshot;
- minimal seed from `configs/gepa_initial_rules_minimal.md`;
- epoch-shuffled train minibatches of 12;
- instance-level validation Pareto selection;
- two cumulative candidate-proposal iterations;
- balanced accuracy;
- independent Apptainer Slurm tasks, with array throttle 4;
- GEPA adapter parallelism 2.

The minibatch remains 12 so each proposal must account for a meaningful
cross-case sample rather than a very small, noisy batch. Two iterations are an
HPC execution pilot, not a sufficient rule-quality experiment. Raw trajectories
need not all be read in full: every case receives a structured review, every
error receives a deeper diagnosis, and correct cases provide regression checks.

`max_iterations=2` is the primary stop condition. GEPA's official saved state
is cumulative, so resuming the same logical run continues toward two total
proposals rather than adding another two. The worst-case planned evaluation
count is `98 + 2 * (12 + 12 + 98) = 342`. `max_metric_calls=500` is a
fail-safe, not the experiment target. Exhausting it before two proposals is
an anomaly to investigate rather than a reason to expand the budget silently.

The current run uses the shared iteration-target supervisor contract. A local
`tmux + caffeinate` service submits short controller allocations. A controller
submits fingerprinted Checker arrays or one Reflection Agent task and then
yields; the next controller replays the same GEPA call and collects the atomic
output.

The run manifest permits a metric-call ceiling increase but rejects changes to
data, source, prompts, model/search semantics, seed, or iteration target.

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
attempt directory.
Slurm terminal state and temporarily missing accounting records wait through
configured output/missing grace periods before retry or failure. GEPA state,
not Slurm task completion, remains the authority for whether a result affected
the search.

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

## Evidence And State

The run directory preserves:

- `run_manifest.json` for immutable logical-run identity;
- `gepa_state.bin` and `gepa_resume_state.json` for official GEPA plus sampler,
  selector, Reflection, and accepted-candidate resume state;
- `iteration_progress.json` and `controller_status.json` for the shared
  supervisor contract;
- `hpc_tasks/checker/<fingerprint>/` and
  `hpc_tasks/reflection/<fingerprint>/` for immutable inputs, per-attempt
  evidence, task state, atomic initial output, and nested fingerprinted repair
  task when required;
- `progress.json`, `audit_events.jsonl`, and `evaluations.jsonl`;
- every Checker call's complete trajectory;
- `reflection_inputs/*/reflection_trajectory.json` inside per-proposal evidence
  bundles;
- `reflection_analysis.json` when the Agent produced parseable JSON diagnostic
  evidence, or `reflection_analysis_invalid.txt` when malformed;
- local `reflection_inputs/*/reflection_repair_trajectory.json`, or HPC repair
  attempt evidence, when a proposal requires contamination repair;
- candidate rules, validation metrics, reports, errors, token use, and cost.

Operational Checker exhaustion currently stops the optimization and is marked
resumable. It must not be converted into an unresolved prediction or silently
included in candidate comparison.

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
