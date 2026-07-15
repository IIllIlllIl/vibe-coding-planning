# Historical Output Archive

This tree is excluded from the default active experiment surface. Read it only
for an explicitly requested historical comparison, audit, or reproduction.

| Directory | Contents | Score use |
|---|---|---|
| `online-gepa/reference/` | Completed Online GEPA smoke and HPC resource pilots | Pipeline/resource reference only |
| `offline-gepa/` | Strict Checker GEPA runs and pre-strict predecessors | Historical offline methodology only |
| `pct/` | SWE-bench Verified, PolyBench, and Pro Plan-Code-Test runs | Historical execution evidence |
| `pcc/` | Checker comparisons, reflection cases, and PCC inputs | Historical checker evidence |
| `analysis/` | Flash/Pro/Kimi rule extraction and reflection analyses | Historical derived analysis |
| `datasets/historical/` | Pilot and Checker snapshots not used by the formal Online run | Immutable reproduction inputs |
| `tests/` | Smoke, prompt-fix, and early integration outputs | No quality claim |
| `operations/` | Watchdog, preheat, and local run logs | Operational evidence only |
| `failed-or-invalid/` | Runs with known invalid scores or unusable terminal state | Never use for scoring |

Archive policy:

- Move whole run directories; do not split checkpoints from their manifests.
- Preserve original-path mappings in `../catalog.json`.
- Historical valid and invalid runs remain separate.
- Archived configs may be used as examples, but a reproduction must use a new
  run directory and must not overwrite archived evidence.
- A run returns to the active surface only through an explicit methodology
  decision, not because a script happens to reference its old path.
