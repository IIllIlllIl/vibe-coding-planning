# Config Guide

The top level contains only active configs and one representative executable
config for each paused workflow. Date-specific runs, smoke tests, retries, and
superseded variants live under `archive/` and remain available for provenance.

## Active Online GEPA

| Config | Runtime | Purpose |
|---|---|---|
| `gepa_online_planning_hpc.yaml` | ULHPC Apptainer | Standard formal configuration over the 384/98 split. Each rollout is an independent `1 CPU / 4G / 55min` array task under outcome policy v3: a Slurm-confirmed timeout retries only that index up to the shared attempt limit, then becomes unresolved; infrastructure failures remain invalid. Fingerprinted batch journals and Plan/Code/Evaluator checkpoints support selective retry. |
| `online_gepa_supervisor.yaml` | Local tmux+caffeinate | Exact formal launch identity: session/log, 8-iteration target, 30-minute cadence, unlimited controller slices, remote workdir, and `1 CPU / 4G / 2h` controller arguments. Use this file instead of reconstructing a long command. |
| `gepa_online_planning_pilot.yaml` | local Docker | Standard small executable example for validating the Online GEPA flow locally. |

Online GEPA is the current mainline. Candidate rules go only to the Plan Agent;
Code receives the issue and generated plan, and Reflection receives evidence
created by the current rollout.

The runtime config and launch config have separate authority: changing prompts,
budgets, evaluator, or worker resources belongs in
`gepa_online_planning_hpc.yaml`; changing the run identity, iteration target,
poll cadence, controller resources, or remote workdir belongs in
`online_gepa_supervisor.yaml`. Keep their run identity and job prefix aligned.

## Representative Paused Workflows

| Config | Historical workflow represented |
|---|---|
| `gepa_verified_rules.yaml` | Offline GEPA strict Checker optimization. |
| `polybench_full199_pct.yaml` | PCT/PCC-era PolyBench data collection and checker evaluation. |
| `analysis_kimi_opencode.yaml` | Per-case rule extraction/aggregation analysis. |
| `gepa_initial_rules_gpt_seed.md` | Shared immutable seed used by representative GEPA configs. |

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
