# Output Workspace

This directory exposes the current Online and Offline GEPA experiment surface.
Agents should not search `output/archive/` unless a task explicitly asks for
historical comparison, provenance, or reproduction.

## Active Surface

| Path | Purpose | Status |
|---|---|---|
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Immutable formal 384/98 snapshot shared by current Online and Offline experiments | Active input; do not move or modify |
| `SWE-bench_Verified/gepa-rules/` | Local destination for current Online and Offline GEPA results | Active output root |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` | Latest local Offline result under the final pre-HPC experimental flow | Completed with warnings; sole active Offline checkpoint and analysis baseline |
| Remote `offline-plan-verifier-hpc-balanced-b12-2it-supervisor-fix-20260729` run directory | Current Offline HPC completion-contract diagnostic | Active 2-proposal test; must use its own persistent identity |
| Remote `online-planning-hpc-policy-v3-20260715` run directory | Outcome-policy-v3 formal run targeting 8 durable iterations | Active; managed by the supervisor |

The active Online rule-generation flow is:

```text
task -> Plan Agent with candidate rules -> Code Agent -> evaluator
     -> structured outcome -> GEPA reflection -> updated planning rules
```

The active Offline rule-generation flow is:

```text
issue + historical plan + base repository + candidate rules
  -> repo-grounded Checker -> balanced-accuracy score
  -> GEPA reflection -> updated plan-approval rules
```

PCT, PCC/Checker, earlier Offline GEPA runs, standalone rule extraction, old
tests, and preheat logs are historical. They are intentionally absent from the
active surface.

## Offline Result Boundary

Only
`offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` remains in
the active result root. It completed 8 proposal iterations with 670 metric
calls, 4 accepted candidates, best candidate 1, and one recorded Reflection
failure. Its complete manifest, GEPA state, raw Checker trajectories,
Reflection inputs, and derived reports remain together.

The checkpoint is structurally resume-capable only under its stored semantic
manifest and the same pre-HPC experiment semantics. The configured
`max_iterations=8` target has already been reached, so an identical resume is a
no-op rather than a new proposal. Increasing the iteration target changes the
semantic manifest and is rejected by the current compatibility check. Any
future extension therefore requires an explicit methodology decision; it must
not be achieved by pointing the current 2-iteration HPC config, changed
prompt/source hashes, or a new run identity at this directory.

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
