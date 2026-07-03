# Config Guide

This directory contains both active experiment configs and historical configs.
For GEPA rule optimization, prefer the configs listed here instead of guessing
from file names.

## Active GEPA Rule Optimization

| Config | Runtime | Purpose |
|---|---|---|
| `gepa_verified_rules.yaml` | local Docker | Default active local prompt-fix run. Uses GPT seed rules, strict Checker, prompt-fix Reflection checklist prompt, full Verified snapshot, and `max_metric_calls: 3000`. Local Docker uses `parallel: 1`, higher Checker retries, and a fresh `p1` run_dir to avoid the earlier Docker Desktop image-pull failure state. |
| `gepa_verified_rules_strict_local_newprompt_3000.yaml` | local Docker | Explicit name for the same local prompt-fix 3000 run. Use this when comparing against earlier strict-only runs. Keeps local Docker checker execution serial for reliability and writes to the same fresh `p1` run_dir. |
| `gepa_verified_rules_strict_hpc_24h_newprompt_20260625_apptainer.yaml` | ULHPC Apptainer | Active HPC prompt-fix run. Uses the shared scratch SIF cache and `parallel: 4`. |
| `gepa_online_planning_pilot.yaml` | local Docker | Experimental online GEPA planning run. Candidate rules go to the Plan Agent as a strict planning checklist; Code Agent only sees the generated plan. Use only for small pilot validation. |

The prompt-fix configs are the current mainline. They differ from the older
strict-only run by using the newer Reflection prompt that maintains rules as a
plan-review checklist, asks it to delete misleading rules, merge redundant
rules, and organize the complete output under light headings.

## Historical GEPA Validation

Keep these files available because docs or tests still reference them:

| Config | Status |
|---|---|
| `gepa_verified_rules_pilot.yaml` | Historical empty-seed pilot. |
| `gepa_verified_rules_pilot_extended.yaml` | Historical longer empty-seed pilot; used by tests. |
| `gepa_verified_rules_reflection_smoke.yaml` | Historical local Reflection smoke; used by tests. |
| `gepa_verified_rules_reflection_smoke_apptainer.yaml` | Historical Apptainer smoke; used by tests and preheat examples. |
| `gepa_verified_rules_strict_hpc_24h_apptainer.yaml` | Previous strict Checker HPC run kept for comparison with prompt-fix. |
| `gepa_verified_rules_formal_pilot_apptainer.yaml` | Previous Apptainer formal pilot referenced by HPC documentation. |

## Archive

`archive/gepa_legacy/` contains configs that are no longer active and are not
used by current tests or scripts. They are kept for provenance only.

## Model Safety

Current GEPA rule-optimization configs should use `deepseek-v4-flash` for both
Checker and Reflection. Do not add `deepseek-v4-pro`, Kimi, or other providers
to GEPA configs unless the experiment explicitly requires it and the run plan
documents why.
