# Output Workspace

This directory exposes only the current Online GEPA experiment surface. Agents
should not search `output/archive/` unless a task explicitly asks for historical
comparison, provenance, or reproduction.

## Active Surface

| Path | Purpose | Status |
|---|---|---|
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Immutable formal 384/98 Online GEPA dataset snapshot | Active input; do not move or modify |
| `SWE-bench_Verified/gepa-rules/` | Local destination for current Online GEPA results | Active output root |
| Remote `online-planning-hpc-policy-v3-20260715` run directory | Outcome-policy-v3 formal run targeting 8 durable iterations | Active; managed by the supervisor |

The active rule-generation flow is:

```text
task -> Plan Agent with candidate rules -> Code Agent -> evaluator
     -> structured outcome -> GEPA reflection -> updated planning rules
```

PCT, PCC/Checker, offline GEPA, standalone rule extraction, old tests, and
preheat logs are historical. They are intentionally absent from the active
surface.

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
4. Keep current Online GEPA run directories and the formal dataset snapshot
   outside `archive/`.
5. Put new smoke/test outputs under an explicitly named test path and archive
   them after the test is reviewed.

Approximate post-reorganization storage:

| Classification | Size |
|---|---:|
| Active formal dataset | 354M |
| Historical PCT | 670M |
| Historical offline GEPA | 239M |
| Historical PCC/Checker | 151M |
| Tests | 141M |
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
