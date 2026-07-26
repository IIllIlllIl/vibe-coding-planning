# Current Offline GEPA

> Authority: current local Offline GEPA experiment contract
>
> Last reviewed: 2026-07-27

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
exact matches for one generalization repair. A clean repair is returned to
GEPA. If that single repair still contains a match, the proposal fails and GEPA
retains its parent; there is no additional judge or unbounded retry.

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
- eight cumulative candidate-proposal iterations;
- balanced accuracy;
- local Docker execution with two concurrent Checkers.

The minibatch remains 12 for the first complete-case-review experiment. Eight
iterations can therefore expose up to 96 epoch-shuffled training positions,
whereas reducing the batch would simultaneously reduce coverage and increase
parent/proposal comparison noise. Raw trajectories need not all be read in
full: every case receives a structured review, every error receives a deeper
diagnosis, and correct cases provide regression checks.

`max_iterations=8` is the primary stop condition. GEPA's official saved state
is cumulative, so resuming the same logical run continues toward eight total
proposals rather than adding another eight. The worst-case planned evaluation
count is `98 + 8 * (12 + 12 + 98) = 1,074`. `max_metric_calls=2000` is a
fail-safe, not the experiment target. Exhausting it before eight proposals is
an anomaly to investigate rather than a reason to expand the budget silently.

The local Offline run does not use the Online HPC supervisor. A restart uses
the same command and run identity after confirming the failure is resumable.
The run manifest permits a metric-call ceiling increase but rejects changes to
data, source, prompts, model/search semantics, seed, or iteration target.

## Evidence And State

The run directory preserves:

- `run_manifest.json` for immutable logical-run identity;
- `gepa_state.bin` and `gepa_resume_state.json` for official GEPA plus sampler,
  selector, Reflection, and accepted-candidate resume state;
- `progress.json`, `audit_events.jsonl`, and `evaluations.jsonl`;
- every Checker call's complete trajectory;
- `reflection_inputs/*/reflection_trajectory.json` inside per-proposal evidence
  bundles;
- `reflection_analysis.json` when the Agent produced parseable JSON diagnostic
  evidence, or `reflection_analysis_invalid.txt` when malformed;
- `reflection_inputs/*/reflection_repair_trajectory.json` when a proposal
  requires the single contamination repair;
- candidate rules, validation metrics, reports, errors, token use, and cost.

Operational Checker exhaustion currently stops the optimization and is marked
resumable. It must not be converted into an unresolved prediction or silently
included in candidate comparison.

## Local Entry Point

Only run after explicit authorization and after confirming the new run identity
does not already contain incompatible state:

```bash
conda run -n mini-swe python -m src.optimization \
  --config configs/gepa_verified_rules.yaml
```

No Offline supervisor is required for the configured eight-iteration run.
