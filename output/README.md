# Output Workspace

This directory exposes the current Online and Offline GEPA experiment surface.
Agents should not search `output/archive/` unless a task explicitly asks for
historical comparison, provenance, or reproduction.

## Active Surface

| Path | Purpose | Status |
|---|---|---|
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Immutable formal 384/98 snapshot shared by current Online and Offline experiments | Active input; do not move or modify |
| `SWE-bench_Verified/gepa-rules/` | Local destination for current Online and Offline GEPA results | Active output root |
| `../configs/frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/` | Exact common seed, minibatch-eight candidates 1/2, and 3x3 candidates 3/6 | Active tracked evaluation bundle; three primary and two reserve guidelines |
| `archive/operations/polybench-python199-v1.1-20260811/` | Local mirror of the completed official `:v1.1` Python-199 availability operation | Operational preparation evidence; remote working copy removed |
| `SWE-PolyBench/polybench-pce-inputs/20260814_python113_v11_8c7d9485d1d0/` | Official Python rows joined to the reviewed exact-`v1.1` availability evidence | Complete immutable formal PCE input; 113 cases |
| `SWE-PolyBench/polybench-pce-runs/formal/python113-v11-pce-20260814/` | Complete local mirror of formal PCE trajectories, attempts, checkpoints, evaluator evidence and controller state | Raw formal evidence; 113 cases, not a Checker input |
| `SWE-PolyBench/polybench-guideline-validation-datasets/20260815_python111_testparsed_26dad63b5cf3/` | Derived PolyBench Checker-only validation snapshot | Active immutable input; 111 parsed-test cases |
| `SWE-PolyBench/polybench-pcce-runs/smoke/` | PCCE orchestration, review-loop, checkpoint and PC-to-CE platform evidence | Planned smoke output root; never formal validation evidence |
| `SWE-PolyBench/polybench-pce-inputs/20260811_smoke2_transformers/` | Frozen two-row source and reviewed image-manifest copy staged to the PCE smoke | Smoke input only; not formal input |
| `archive/tests/polybench-pce/` | Locally mirrored smoke3 diagnostic evidence and completed smoke4 evidence | Test only; remote working copies removed |
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

The replacement PolyBench input, formal PCE evidence and cleaned validation
snapshot are frozen. Their artifact classes must not be mixed:

| Root | Classification | May be used as formal validation data? |
|---|---|---|
| `archive/operations/polybench-python199-v1.1-20260811/` | Frozen local mirror of operational download provenance and failures | No |
| `SWE-PolyBench/polybench-pce-inputs/` | Frozen reviewed source/image inputs staged to PCE | Yes only for snapshots whose manifest declares complete and non-provisional |
| `SWE-PolyBench/polybench-pce-runs/smoke/` | Platform-smoke trajectories, patches, evaluator and controller evidence | No |
| `SWE-PolyBench/polybench-pce-runs/formal/` | Formal raw PCE evidence, including failed attempts and operational outcomes | No; derive a reviewed validation snapshot first |
| `SWE-PolyBench/polybench-guideline-validation-datasets/` | Labelled validation inputs derived by a frozen, guideline-independent cleaning policy | Yes, for Checker-only external validation; never for GEPA/Reflection |
| `SWE-PolyBench/polybench-guideline-validation-guidelines/` | Guidelines frozen before PolyBench results are viewed | Yes, as immutable model inputs only |
| `SWE-PolyBench/polybench-pcce-runs/smoke/` | PCCE platform-flow evidence using provisional prompts | No |
| `SWE-PolyBench/polybench-pcce-runs/formal/` | Future paired deployment-oriented guideline evaluation | Only after prompt/schema, guideline selection, metric, and threshold are frozen |
| `../configs/frozen_guidelines/` | Compact tracked guideline bundles with exact text and multi-run provenance | Yes; current authority for the staged five-guideline evaluation |

The complete formal input snapshot is
`20260814_python113_v11_8c7d9485d1d0`: 113 exact `v1.1` SIFs are available and
86 references are confirmed manifest-not-found after authenticated retry. It
freezes the official source rows, complete 199-record image provenance and
86-record unavailability evidence together. The exclusions are environment
availability decisions with null research labels. The two-instance smoke is
likewise a test of transport, phase
isolation and evidence preservation; its resolved labels and plans must not be
included in the future PolyBench generalization score.

Smoke4 worker array `5661319` completed 2/2 cases on attempt 1. Collection
controller `5670870` reused those outputs without submitting new workers and
recorded 2 complete, 0 incomplete, 1 resolved and 1 unresolved. This closes the
platform-smoke prerequisite; it does not make the smoke a validation result.

The formal run subsequently completed all three-attempt controller semantics.
Of 113 source cases, 111 produced parsed test outcomes, one Evaluate command
timed out, and one Code task exhausted three attempts. The complete raw run is
mirrored locally. The active derived snapshot excludes those two non-parsed
outcomes without manufacturing labels, then applies the same resolved-only,
non-empty-patch placeholder policy used for SWE-bench Verified 500-to-482.
No PolyBench plan matched that placeholder policy, leaving 111 cases: 59
resolved and 52 unresolved.

The first PCE smoke preserved valid Plan and Code checkpoints for both cases
but failed in Evaluate because the Host deleted its own evaluator inputs during
repository cleanup. It is retained as failed smoke evidence, not an active
resume target. Evaluator-source changes alter the PCE fingerprint, so the old
batch must not be advanced as attempt 2 under new code. Any future
Evaluate-only reuse must have a new identity and explicitly cite the old
checkpoint hashes; a complete new smoke remains necessary for end-to-end
acceptance. That requirement was later satisfied by smoke4; the failed smoke
itself remains ineligible for resume or validation use.

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
