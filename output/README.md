# Output Workspace

> Authority: current artifact locations, lifecycle classification, and
> interpretation boundary. Exact structured metadata lives in `catalog.json`.

Agents should not search `output/archive/` unless a task explicitly requests
historical comparison, provenance, or reproduction. Frozen raw evidence must
not be edited in place.

## Behavioral Plan Acceptability

| Path | Classification | Status and permitted use |
|---|---|---|
| `../configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Frozen compact input authority | Stage-1 selection, Stage-2 first-Plan slicing, repository availability, reconstruction audit, and temporal-proxy manifests |
| `SWE-chat/behavioral-gepa-datasets/formal-repository-holdout-v1-20260830/` | Superseded formal input | Pre-media-projection provenance only; do not launch |
| `SWE-chat/behavioral-gepa-datasets/formal-repository-holdout-media-projected-v1-20260830/` | Frozen formal input | 84 train / 47 validation; validation participated in candidate selection and is not an untouched holdout |
| Remote `/scratch/users/twang/vibe-coding-planning/behavioral-gepa-run-state/output/SWE-chat/behavioral-gepa-smoke-v2-20260830/stage-c-hpc-gepa-smoke-v2/` | Test evidence | Completed one-proposal platform/prompt smoke; no effectiveness claim |
| Remote `/scratch/users/twang/vibe-coding-planning/behavioral-gepa-run-state/output/SWE-chat/behavioral-gepa-runs/formal/repository-holdout-media-projected-accuracy-b8-8it-v2-20260830/` | Formal candidate-selection evidence | Completed eight iterations and 410 logical metric calls with no incomplete validation prediction; not untouched generalization evidence |

The tracked exact C4 guideline is under
`../configs/frozen_guidelines/behavioral-formal-c4-v1-20260831/`. Stable result
interpretation belongs to
`../docs/knowledge/behavioral-gepa-initial-findings.md`; this file only locates
artifacts.

## SWE-Verified Quick Validation

| Path | Classification | Status and permitted use |
|---|---|---|
| `SWE-bench_Verified/swe-verified-pce-runs/quick-validation/current-prompt-quick50-v1-20260901/` | Paired quick-validation baseline | Completed 50-case current-prompt PCE; exact Plans and terminal outcomes are inputs to both PCCE methods |
| `SWE-bench_Verified/swe-verified-pcce-runs/quick-validation/neutral-seed-v1-20260901/` | Paired quick-validation evidence | Completed Seed PCCE; local copy contains terminal outcomes, reviews, manifest, and summary while phase-level raw evidence remains on Iris |

This coverage-oriented 50-case cohort is development evidence, not an
untouched holdout or a prevalence estimate. C4 is prepared but has no output
until separately authorized and launched. Stable method and result semantics
belong to `../docs/swe-verified-pce-pcce.md`.

## Frozen Offline And PolyBench Evidence

| Path | Classification | Status and permitted use |
|---|---|---|
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Frozen retained Offline input | Historical Offline reproduction only |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` | Formal local Offline baseline | Completed with warnings; frozen comparison baseline |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731/` | Formal HPC Offline baseline | Stopped after 14 durable proposals; frozen analysis baseline, not resumable under the Behavioral method |
| `SWE-PolyBench/polybench-pce-runs/formal/python113-v11-clean-boundary-v1-20260825/` | Frozen clean-boundary PCE raw evidence | Source for the reviewed paired validation snapshot |
| `SWE-PolyBench/polybench-guideline-validation-datasets/20260826_python99_cleanpce_depcache_03619730229d/` | Frozen paired PCE/PCCE input | 99 cases: 70 resolved / 29 unresolved after accepted evaluator repair |
| `SWE-PolyBench/polybench-pcce-runs/formal/seed-python99-clean-pce-v1-20260826/` | Frozen PCCE evidence | Seed: 66/99 after accepted repair |
| `SWE-PolyBench/polybench-pcce-runs/formal/b8-candidate2-python99-clean-pce-v1-20260826/` | Frozen PCCE evidence | Candidate 2: 66 resolved / 32 unresolved / 1 incomplete after accepted repair |
| Remote `~/hpc_run_state/vibe-coding-planning/output/SWE-PolyBench/polybench-pc-checker-only-runs/development/c4-balanced20-v1-20260831/` | Development external diagnostic | C4 13/20 versus historical Seed 12/20; one Checker review only, with no Planner, Code, or Evaluate |

The initial clean PCCE stage is closed. PolyBench results must not enter the old
GEPA candidate tree, and the 20-case C4 subset is neither a population estimate
nor an untouched holdout. See `../docs/knowledge/offline-pcce-stage-findings.md`
and `../docs/knowledge/behavioral-gepa-initial-findings.md` for conclusions and
limitations.

## Artifact Boundaries

- Dataset snapshots and manifests define membership; raw run directories retain
  trajectories, retries, and operational evidence.
- Evaluator-repair evidence remains attached to its frozen parent run and must
  not silently replace unrelated labels.
- Smoke and development outputs prove bounded flow properties only.
- Completed configs and run directories are provenance, not authorization to
  resume, extend, or relaunch an experiment.
- `catalog.json` is the machine-readable index. A conflict between this prose
  and a verified frozen manifest must be resolved in favor of the manifest and
  corrected here.

## Archive Boundary

`archive/` contains historical, test-only, operational, and invalid artifacts.
Archiving does not delete evidence or make its scores current. Exact historical
paths remain available through `catalog.json`, the archive README, and
`main@95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1`.
