# Config Guide

The top level contains only active configs and one representative executable
config for each paused workflow. Date-specific runs, smoke tests, retries, and
superseded variants live under `archive/` and remain available for provenance.

## Active Online GEPA

| Config | Runtime | Purpose |
|---|---|---|
| `gepa_online_planning_hpc.yaml` | ULHPC Apptainer | Standard formal configuration over the 384/98 split. PCT, repo-grounded Reviewer, and Synthesis are separate Slurm phases, each using `1 CPU / 4G / 55min` and at most three attempts. Reviewer/Synthesis preserve per-attempt trajectories; Synthesis no longer consumes controller walltime. |
| `online_gepa_supervisor.yaml` | Local tmux+caffeinate | Exact launch identity for the separate-Reflection 2-iteration smoke: session/log, 30-minute cadence, unlimited controller slices, remote workdir, and `1 CPU / 4G / 2h` controller arguments. The smoke deliberately retains `reflection_minibatch_size: 3`. It is configured but not launched. |
| `gepa_online_planning_pilot.yaml` | local Docker | Standard small executable example for validating the Online GEPA flow locally. |

Online GEPA is the current mainline. Candidate rules go only to the Plan Agent;
Code receives the issue and generated plan, and Reflection receives evidence
created by the current rollout.

Code may write diagnostic tests in its isolated workspace and is responsible
for staging the final submission it wants the clean official evaluator to run.
The Host requires a formal non-empty submission and preserves its bytes across
checkpoint/worker transfer, but does not filter paths or repair the patch.
Malformed, incomplete, or poorly selected patches are evaluator-visible Code
outcomes and normally score unresolved rather than blocking the run.

The SIF instance reviewer may also run focused tests or make temporary
diagnostic/counterfactual edits after inspecting the untouched base. These edits
are discarded with its SIF overlay. Its full trajectory and the immutable
rollout evidence remain available to synthesis; no Host semantic command ledger
or repository-state classifier sits between the two agents.

The runtime config and launch config have separate authority: changing prompts,
budgets, evaluator, or worker resources belongs in
`gepa_online_planning_hpc.yaml`; changing the run identity, iteration target,
poll cadence, controller resources, or remote workdir belongs in
`online_gepa_supervisor.yaml`. Keep their run identity and job prefix aligned.

## Offline GEPA

| Config | Runtime | Purpose |
|---|---|---|
| `gepa_verified_rules.yaml` | ULHPC Apptainer | Formal `20260731` run extended from 8 to 20 cumulative proposals: full 384/98 split, minibatch 12, balanced accuracy, minimal seed, three task attempts, and `1 CPU / 4G / 35min` per Agent. The extension retains the original run directory and GEPA state. |
| `offline_gepa_supervisor.yaml` | Local tmux+caffeinate | Matching 20it extension launch identity. It uses 10-minute polling, `1 CPU / 4G / 10min` controller slices, and requires an unchanged runtime-config hash, Git commit, and clean worktree before submission. |
| `offline_gepa_hpc_smoke_2x2_20260728.yaml` | ULHPC Apptainer | Environment-only 2-train/2-validation, 1-iteration smoke. Its scores are not rule-quality evidence. |
| `gepa_initial_rules_minimal.md` | Prompt text | Minimal current Offline seed intended to leave room for GEPA exploration. |

`search.max_iterations` is the primary experimental stop condition and is an
absolute cumulative proposal target across resume. `max_metric_calls=500` is
only a fail-safe above the 342-call worst-case projection. Offline uses its own
launch config with the shared supervisor service. See
[`../docs/offline-gepa.md`](../docs/offline-gepa.md).

## Representative Paused Workflows

| Config | Historical workflow represented |
|---|---|
| `polybench_full199_pct.yaml` | PCT/PCC-era PolyBench data collection and checker evaluation. |
| `analysis_kimi_opencode.yaml` | Per-case rule extraction/aggregation analysis. |
| `gepa_initial_rules_gpt_seed.md` | Immutable historical seed retained with provenance; not used by the current Offline config. |

## Archive

| Directory | Contents |
|---|---|
| `archive/online_tests/` | Date-specific Online GEPA smoke, resource pilot, and invalid resume configs. |
| `archive/offline_gepa/` | Offline GEPA pilots, smoke tests, alternate runtimes, and superseded rule seeds. |
| `archive/pct_runs/` | PCT continuation, completion, and retry manifests/configs. |
| `archive/gepa_legacy/` | Earlier superseded offline GEPA configuration. |

Archived configs are intentionally executable for reproduction, but they are
not defaults and should not be copied into a new experiment without reviewing
their output path and historical failure context.

## Model Safety

Current GEPA rule-optimization configs should use `deepseek-v4-flash` for both
Checker and Reflection. Do not add `deepseek-v4-pro`, Kimi, or other providers
to GEPA configs unless the experiment explicitly requires it and the run plan
documents why.
