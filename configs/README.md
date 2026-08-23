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
| `offline_gepa_hpc_smoke_3x3_2it_20260815.yaml` | ULHPC Apptainer | Retained identity of the stopped cross-snapshot-manifest diagnostic. It blocked at iteration 0 before train repetition; do not reuse it for the post-fix smoke. |
| `offline_gepa_supervisor_3x3_2it_20260815.yaml` | Local tmux+caffeinate | Matching stopped supervisor identity retained for diagnosis; a post-fix smoke requires a new launch and run identity. |
| `offline_gepa_hpc_smoke_3x3_2it_postfix_20260816.yaml` | ULHPC Apptainer | Exact completed post-fix 3 x 3 baseline. It reached the cumulative two-proposal target and is retained unchanged as the first stage of the planned 2-to-8 extension. |
| `offline_gepa_supervisor_3x3_2it_postfix_20260816.yaml` | Local tmux+caffeinate | Exact stopped supervisor identity for the completed two-proposal stage; do not restart it with a different target. |
| `offline_gepa_hpc_3x3_8it_extension_20260816.yaml` | ULHPC Apptainer | Cumulative eight-proposal target for the same post-fix checkpoint and candidate tree. Only the target, metric-call fail-safe/projection, and operational task config identity differ from the retained two-proposal runtime config. |
| `offline_gepa_supervisor_3x3_8it_extension_20260816.yaml` | Local tmux+caffeinate | New clean-worktree-guarded supervisor identity that performs the native 2-to-8 iteration-target transition, then resumes the ordinary Offline flow against the same persistent run directory. |
| `frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/` | Frozen text bundle | Tracked five-guideline PolyBench input frozen before check-only results: common seed plus minibatch-eight candidates 1/2 and 3x3 candidates 3/6. Primary evaluation uses seed and the two accuracy winners; the two direct-parent alternatives are reserve inputs. |
| `polybench_pcce_hpc_smoke.yaml` | ULHPC Apptainer | Two-case PCCE platform-flow smoke using paired historical PCE plans, the frozen seed guideline, three workflow task attempts, and a distinct three-valid-rejection experimental budget. It owns dedicated Checker decision/feedback and Planner revision prompts; smoke evidence is not yet a formal method result. |
| `polybench_pcce_supervisor_smoke.yaml` | Local tmux+caffeinate | Persistent launch identity for the two-case PCCE smoke. It reuses the shared HPC resume loop at the same ten-minute cadence as Offline and repeatedly submits 10-minute PCCE Controller slices until the workflow reaches a terminal result or blocking failure. |
| `polybench_pcce_hpc_formal_seed.yaml` | ULHPC Apptainer | Formal PCCE evaluation of the frozen seed guideline over every member of the cleaned 111-case PolyBench snapshot. It freezes the smoke-accepted prompts, three valid-rejection method budget, three workflow attempts, and uncapped one-case Slurm arrays under a new formal run identity. |
| `polybench_dependency_preheat_20260822.yaml` | Iris login node | Frozen 23-case evaluator-dependency preheat plan. Each case uses its exact official-v1.1 SIF and an evidence-derived download profile; the separate cache does not modify the SIF or any PCE/PCCE result. |
| `polybench_dependency_preheat_smoke_v2_20260823.yaml` | Iris login node | Corrective three-case cache preparation: Hub artifacts retain a `main` ref, while LangChain uses its exact legacy SentenceTransformer layout and includes both models exercised by the tests. |
| `polybench_pce_hpc_dependency_cache_smoke.yaml` | ULHPC Apptainer | Three-case Evaluate-only smoke over preserved PCE Plan/Code checkpoints. It binds the frozen dependency cache read-only, disables container networking, and invokes no LLM. |
| `polybench_pce_hpc_dependency_cache_smoke_v2.yaml` | ULHPC Apptainer | Corrective three-case Evaluate-only smoke using the loader-compatible v3 cache snapshot and a new repair identity. |
| `frozen_dependency_caches/polybench_evaluator_dependencies_20260822/` | Frozen input | Tracked file/revision/hash manifest for 69 prepared artifact requests; its membership contains 22 eligible cases and explicitly excludes `transformers-25636` because one required repository was inaccessible. |
| `polybench_pcce_supervisor_formal_seed.yaml` | Local tmux+caffeinate | Clean-worktree-guarded formal seed launch identity. It advances the formal PCCE run with 10-minute Controller slices and polling while preserving the runtime config as method authority. |
| `polybench_pcce_supervisor_formal_seed_contract_retry_20260818.yaml` | Local tmux+caffeinate | Staged operational-resume identity after the contract-error classification fix. It keeps the exact formal runtime config and remote run directory, uses a new local session/log/state bound to the repaired source commit, and grants one audited Controller submission from the existing `TaskBatchBlocked` status so the narrow transport reclassification can run. |
| `polybench_pcce_supervisor_formal_seed_evaluator_repair_20260821.yaml` | Local tmux+caffeinate | Native-HOME Evaluate-only repair of the completed formal seed PCCE run. It monitors the independent repair root and reuses fixed PC/Code checkpoints without new LLM calls. |
| `gepa_guideline_accuracy_b12_20260806_candidate1.md` | Prompt text | Exact best candidate from `offline-plan-guideline-hpc-accuracy-b12-8it-checker-timeout30m-formal-20260806`: candidate 1, validation accuracy `73/98`, semantic SHA-256 `17e8d1c1e0f96e53b8568fd28ca63d8525ca04911da6e0c604324297bfab9925`. |
| `gepa_initial_guideline_minimal.md` | Prompt text | Default-accept minimal Offline guideline seed. It rejects only when available evidence clearly shows a material problem, while supplying no repository-investigation behavior, format, or review methodology. |
| `gepa_initial_rules_minimal.md` | Historical prompt text | Frozen pre-guideline seed retained for archived configs and provenance. |

`search.max_iterations` is the primary experimental stop condition and is an
absolute cumulative proposal target across resume. For the eight-
iteration draft, `max_metric_calls=1200` is only a fail-safe above the
1010-call worst-case projection. Offline uses its own
launch config with the shared supervisor service. See
[`../docs/offline-gepa.md`](../docs/offline-gepa.md).

The generic check-only entry point is additive. The reviewed 113-case
source/exact-v1.1 input is now frozen, but no PolyBench check-only config is
active until formal PCE data and its separately reviewed cleaning snapshot are
frozen. The existing `gepa_verified_rules.yaml`
remains byte-for-byte unchanged, so neither the GEPA semantic manifest nor the
supervisor's raw-config identity is invalidated.

`polybench_pce_hpc_smoke.yaml` is an isolated two-instance platform-smoke
configuration for the new raw PCE workflow. It selects only completed,
hash-frozen `:v1.1` SIFs and is launched by
`scripts/hpc_submit_polybench_pce.sh`. Its outputs are operational evidence,
not formal PolyBench validation data, and it does not call GEPA, Reflection, or
the Online rollout workflow. Smoke results live below
`output/SWE-PolyBench/polybench-pce-runs/smoke/`; future formal PCE results use
a separate `polybench-pce-runs/formal/` identity. The active config uses the
new `hpc-smoke4-walltime125-resume-boundary-20260813` identity and a 125-minute
hard worker limit. A completed worker exits immediately; no reserved cleanup
window is part of the result contract. It does not resume an earlier smoke.
Worker array `5661319` completed both instances and collection controller
`5670870` reused them without launching another worker; this config remains a
completed smoke rather than the future formal config.
Code may create diagnostic
tests, while PCE preserves the raw submission and evaluates a separately
recorded patch with test paths filtered out.

`polybench_pce_hpc_formal.yaml` is the formal raw-data configuration for the
frozen 113-case exact-`v1.1` input. It uses a separate
`polybench-pce-runs/formal/python113-v11-pce-20260814` identity, submits one
uncapped Slurm element per task, and retains the smoke-proven `1 CPU / 4G /
125min` worker boundary with three total attempts. It does not run GEPA,
Reflection, or guideline evaluation.

`polybench_pcce_hpc_smoke.yaml` is additive and does not alter the completed
PCE or Offline GEPA configs. It reads two members of the frozen 111-case
snapshot, joins their exact historical PCE plans, and writes only below
`polybench-pcce-runs/smoke/`. It is advanced through
`scripts/hpc_submit_polybench_pcce.sh`. Workflow `task_attempt` is controlled
by `hpc.max_task_attempts`; the independent experimental rejection limit is
`pcce.max_review_rejections` and is currently fixed at three. The two cases are
the smallest practical branch-oriented smoke: they do not guarantee both
Checker decisions, but allow proceed and reject/revision behavior to surface
without submitting the formal 111-case evaluation.

Start or inspect the matching unattended supervisor through
`scripts/hpc_supervisor_service.py` with
`configs/polybench_pcce_supervisor_smoke.yaml`. The launch config is operational
identity only; PCCE Agent, prompt, data, rejection-budget, and worker semantics
remain entirely in `polybench_pcce_hpc_smoke.yaml` and the frozen inputs.

The completed two-case smoke exercised both branches: one frozen plan passed
the first review, while one was rejected, revised by the Planner, passed its
second review, and then completed Code/Evaluate. Both CE outcomes were parsed
and resolved, all workflow tasks completed on their first task attempt, and no
Slurm task remained active. The formal seed pair
`polybench_pcce_hpc_formal_seed.yaml` and
`polybench_pcce_supervisor_formal_seed.yaml` therefore preserves the accepted
method and transport while removing only the smoke's two-instance selector.

The completed Python-199 SIF preheater named
`gepa_verified_rules.yaml`, but this is not a GEPA experiment dependency:
`--remote-images-json` replaces its dataset-derived image list, and the config
is read only for the Apptainer runtime and shared SIF-cache path. There is no
dedicated PCE-download config. Do not infer a training-data relationship from
this operational reuse. The completed availability scan accepted exact
official `v1.1` references only; image fallback is not configurable here.

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
