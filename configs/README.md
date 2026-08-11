# Config Guide

The top level contains only active configs and one representative executable
config for each paused workflow. Date-specific runs, smoke tests, retries, and
superseded variants live under `archive/` and remain available for provenance.

## Active Online GEPA

| Config | Runtime | Purpose |
|---|---|---|
| `gepa_online_planning_hpc.yaml` | ULHPC Apptainer | Standard formal configuration over the 384/98 split. PCT, repo-grounded Reviewer, and Synthesis are separate Slurm phases, each using `1 CPU / 4G / 55min` and at most three attempts. Reviewer/Synthesis preserve per-attempt trajectories; Synthesis no longer consumes controller walltime. |
| `online_gepa_supervisor.yaml` | Local tmux+caffeinate | Persisted Online launch identity. It owns the session/log, polling cadence, controller-slice policy, remote workdir, and controller resources; rollout semantics remain in the runtime config. |
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
| `gepa_verified_rules.yaml` | ULHPC Apptainer | Formal eight-iteration Offline GEPA run using the default-accept minimal seed, accuracy, Reflection minibatch eight, and three total fresh-task attempts. |
| `offline_gepa_supervisor.yaml` | Local tmux+caffeinate | Matching formal Offline resume identity. It uses 10-minute polling and `1 CPU / 4G / 10min` controller slices, requires a clean unchanged commit/config, and reads the cumulative eight-proposal target from the runtime config. |
| `offline_gepa_hpc_smoke_2x2_20260728.yaml` | ULHPC Apptainer | Environment-only 2-train/2-validation, 1-iteration smoke. Its scores are not rule-quality evidence. |
| `gepa_guideline_accuracy_b12_20260806_candidate1.md` | Prompt text | Exact best candidate from `offline-plan-guideline-hpc-accuracy-b12-8it-checker-timeout30m-formal-20260806`: candidate 1, validation accuracy `73/98`, semantic SHA-256 `17e8d1c1e0f96e53b8568fd28ca63d8525ca04911da6e0c604324297bfab9925`. |
| `gepa_initial_guideline_minimal.md` | Prompt text | Default-accept minimal Offline guideline seed. It rejects only when available evidence clearly shows a material problem, while supplying no repository-investigation behavior, format, or review methodology. |
| `gepa_initial_rules_minimal.md` | Historical prompt text | Frozen pre-guideline seed retained for archived configs and provenance. |

`search.max_iterations` is the primary experimental stop condition and is an
absolute cumulative proposal target across resume. For the eight-
iteration draft, `max_metric_calls=1200` is only a fail-safe above the
1010-call worst-case projection. Offline uses its own
launch config with the shared supervisor service. See
[`../docs/offline-gepa.md`](../docs/offline-gepa.md).

The generic check-only entry point is additive. No PolyBench check-only config
is active until the new official Python-199 PCE data, exact-v1.1 image manifest,
and cleaning snapshot are frozen. The existing `gepa_verified_rules.yaml`
remains byte-for-byte unchanged, so neither the GEPA semantic manifest nor the
supervisor's raw-config identity is invalidated.

`polybench_pce_hpc_smoke.yaml` is an isolated two-instance platform-smoke
configuration for the new raw PCE workflow. It selects only completed,
hash-frozen `:v1.1` SIFs and is launched by
`scripts/hpc_submit_polybench_pce.sh`. Its outputs are operational evidence,
not formal PolyBench validation data, and it does not call GEPA, Reflection, or
the Online rollout workflow. Smoke results live below
`output/SWE-PolyBench/polybench-pce-runs/smoke/`; future formal PCE results use
a separate `polybench-pce-runs/formal/` identity.

The active Python-199 SIF preheater currently names
`gepa_verified_rules.yaml`, but this is not a GEPA experiment dependency:
`--remote-images-json` replaces its dataset-derived image list, and the config
is read only for the Apptainer runtime and shared SIF-cache path. There is no
dedicated PCE-download config yet. Do not change the formal Offline config to
control an in-flight download, and do not infer a training-data relationship
from this operational reuse.

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
