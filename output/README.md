# Output Workspace

This directory exposes the current Online and Offline GEPA experiment surface.
Agents should not search `output/archive/` unless a task explicitly asks for
historical comparison, provenance, or reproduction.

## Active Surface

| Path | Purpose | Status |
|---|---|---|
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Immutable formal 384/98 snapshot shared by current Online and Offline experiments | Active input; do not move or modify |
| `SWE-bench_Verified/gepa-rules/` | Local destination for current Online and Offline GEPA results | Active output root |
| `SWE-PolyBench/polybench-guideline-validation-guidelines/20260811_seed-c1-c2-c3_3293f8e925b8/` | Exact seed and candidate indices 1-3 from the completed 20260810 Offline run | Frozen evaluation bundle |
| Remote `operations/polybench-python199-v1.1-20260811/` | Incremental official `:v1.1` SIF download provenance for the frozen Python-199 image list | Active operational input preparation; not a dataset or result |
| `SWE-PolyBench/polybench-pce-inputs/20260811_smoke2_transformers/` | Frozen two-row source and reviewed image-manifest copy staged to the PCE smoke | Smoke input only; not the future Python-199 snapshot |
| Remote `SWE-PolyBench/polybench-pce-runs/smoke/hpc-smoke2-20260811/` | Two-instance PCE controller/worker/phase evidence | Active test-only smoke; never guideline-quality evidence |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` | Formal local Offline result under the final pre-HPC experimental flow | Completed with warnings; retained comparison baseline |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731/` | Local mirror of the formal Offline HPC experiment under the frozen strong-Checker/checklist semantics | Stopped after 14 durable proposals during attempted proposal 15; frozen analysis baseline |
| Remote `online-planning-hpc-policy-v3-20260715` run directory | Outcome-policy-v3 formal run targeting 8 durable iterations | Active; managed by the supervisor |

The active Online rule-generation flow is:

```text
task -> Plan Agent with candidate rules -> Code Agent -> evaluator
     -> structured outcome -> GEPA reflection -> updated planning rules
```

The active Offline rule-generation flow is:

```text
issue + historical plan + base repository + candidate guideline
  -> repo-grounded Checker -> configured label-agreement score
  -> GEPA reflection -> updated plan-review guideline
```

PCT, PCC/Checker, earlier Offline GEPA runs, standalone rule extraction, old
tests, and preheat logs are historical. They are intentionally absent from the
active surface.

## PolyBench Preparation And Output Boundary

The replacement PolyBench validation is still being prepared. Its artifact
classes must not be mixed:

| Root | Classification | May be used as formal validation data? |
|---|---|---|
| Remote `operations/polybench-python199-v1.1-20260811/` | Mutable operational download list, incremental provenance, and failures | No |
| `SWE-PolyBench/polybench-pce-inputs/` | Frozen reviewed source/image inputs staged to PCE | Only after the snapshot is declared complete |
| `SWE-PolyBench/polybench-pce-runs/smoke/` | Platform-smoke trajectories, patches, evaluator and controller evidence | No |
| `SWE-PolyBench/polybench-pce-runs/formal/` | Reserved formal raw PCE evidence root | Not until a separately reviewed formal run exists |
| `SWE-PolyBench/polybench-guideline-validation-guidelines/` | Guidelines frozen before PolyBench results are viewed | Yes, as immutable model inputs only |

The live download manifest is incremental and has no completion declaration.
It must not be catalogued as a frozen dataset merely because some SIF records
exist. The active two-instance smoke is likewise a test of transport, phase
isolation and evidence preservation; its resolved labels and plans must not be
included in the future PolyBench generalization score.

## Offline Result Boundary

Two formal Offline results remain in the active result root. The local
`offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` run completed
8 proposal iterations with 670 metric calls, 4 accepted candidates, best
candidate 1, and one recorded Reflection failure. The mirrored HPC
`offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731/` run reached 14
durable proposals, then stopped during attempted proposal 15 when the provider
reported insufficient balance. Its manifest, GEPA state, candidate tree, raw
Checker trajectories, Reflection evidence, and task journals are retained
together locally.

The 20260727 checkpoint is structurally resume-capable only under its stored
semantic manifest and the same pre-HPC experiment semantics. The configured
`max_iterations=8` target has already been reached, so an identical resume is a
no-op rather than a new proposal. Increasing the iteration target changes the
semantic manifest and is rejected by the current compatibility check. Any
future extension therefore requires an explicit methodology decision; it must
not be achieved by pointing the current 2-iteration HPC config, changed
prompt/source hashes, or a new run identity at this directory.

The 20260731 HPC result is complete only as evidence for its frozen semantic
version. It must not be resumed under the planned prompt and metric changes;
the redesigned experiment requires a new run identity. The preceding Offline
HPC platform/behavior runs were smoke tests rather than formal rule-quality
experiments. Their raw outputs are preserved remotely under
`archive/tests/offline-gepa/` and excluded from the default analysis surface:

| Remote archived run | Outcome | Smoke purpose |
|---|---|---|
| `offline-plan-verifier-hpc-balanced-b12-2it-20260728/` | Stopped after 1 durable proposal; Checker task attempts exhausted | Initial controller/worker and failure-path validation |
| `offline-plan-verifier-hpc-balanced-b12-2it-failure-evidence-20260729/` | Produced diagnostic output under superseded supervisor semantics | Failure-evidence and retry validation |
| `offline-plan-verifier-hpc-balanced-b12-2it-identity-guard-20260730/` | Stopped after 1 durable proposal | Identity guard validation; used superseded `%4` array throttling |
| `offline-plan-verifier-hpc-balanced-b12-2it-slurm-native-20260730/` | Completed 2/2 proposals and 342 metric calls | End-to-end Slurm-native scheduling smoke |
| `offline-plan-guideline-hpc-accuracy-b12-6it-smoke-20260804/` | Completed 6/6 proposals and 438 metric calls | Revised Checker/Reflection boundary and standalone-guideline behavior smoke |

These runs may be used to diagnose the HPC implementation, retries,
trajectories, and scheduling behavior. Their candidate scores must not be
presented as formal Offline experimental results. The next formal HPC run must
use a new run identity and must not resume any of these directories.

The two retained remote project snapshots are separately archived under
`~/hpc_runs/archive/tests/offline-gepa-workdirs/`; their result symlinks point
to the archived evidence above.

The older `strict-checker-hpc-24h-20260622/` result remains valid only for its
historical method and is archived remotely under `archive/offline-gepa/`; it is
not relabeled as a smoke and is not part of the current formal result surface.

The other recent Offline directories are archived as follows:

| Archived path | Classification | Reason |
|---|---|---|
| `archive/tests/offline-gepa/offline-interactive-checker-3it-smoke-20260721/` | Test only | Three-iteration smoke with minibatch 3 and parallel 1; no quality claim |
| `archive/offline-gepa/offline-plan-verifier-balanced-b12-8it-20260722/` | Historical valid | Earlier parallel-1 method version; completed with warnings |
| `archive/offline-gepa/offline-plan-verifier-balanced-b12-p2-8it-20260723/` | Historical valid | Earlier parallel-2 method version before per-case Reflection review |

Archived valid results remain usable for explicitly scoped historical
comparison, but they are excluded from default analysis and must not be resumed
under current code/config semantics.

## Archive Boundary

`archive/` contains valid historical evidence as well as separately identified
invalid runs. Archiving does not mean deletion. Its categories are documented
in `archive/README.md`; `catalog.json` records the classification and original
path families.

Rules for agents and scripts:

1. Do not use archived scores as evidence for current Online GEPA quality.
2. Do not resume an archived run without an explicit user request and identity
   validation.
3. Do not mix outcome-policy versions in score comparisons.
4. Keep current Online/Offline GEPA run directories and the formal dataset
   snapshot outside `archive/`.
5. Put new smoke/test outputs under an explicitly named test path and archive
   them after the test is reviewed.

Approximate post-reorganization storage:

| Classification | Size |
|---|---:|
| Active formal dataset | 354M |
| Historical PCT | 670M |
| Historical offline GEPA | 583M |
| Historical PCC/Checker | 151M |
| Tests | 156M |
| Online GEPA references | 55M |
| Invalid runs | 40M |
| Historical analysis | 40M |
| Historical datasets | 722M |
| Operational logs | 4.7M |

The active dataset remains at its established path because formal configs and
the running HPC experiment depend on that identity. Everything else is grouped
by research lifecycle to reduce accidental context pollution. Two superseded
Verified snapshots and their original multi-snapshot index are retained under
`archive/datasets/historical/verified-round1-gepa-datasets/`; the active index
names only the formal `20260614_482_fdc056ae85df` snapshot.

On 2026-07-15, four non-resumable Online runs were moved on the remote host
into `archive/failed-or-invalid/`: `postfix-8h-20260712`,
`formal-8h-20260713`, `supervised-8it-20260714`, and
`policy-v2-20260715`. Their partial outputs are retained for diagnostics but
must not be resumed or compared directly with policy v3.
