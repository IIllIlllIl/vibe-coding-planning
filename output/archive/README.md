# Historical Output Archive

This tree is excluded from the default active experiment surface. Read it only
for an explicitly requested historical comparison, audit, or reproduction.

| Directory | Contents | Score use |
|---|---|---|
| `online-gepa/reference/` | Completed Online GEPA smoke and HPC resource pilots | Pipeline/resource reference only |
| `offline-gepa/` | Strict Checker runs, pre-strict predecessors, and superseded July 2026 Offline plan-verifier runs | Historical offline methodology only |
| `pct/` | SWE-bench Verified, PolyBench, and Pro Plan-Code-Test runs | Historical execution evidence |
| `pcc/` | Checker comparisons, reflection cases, and PCC inputs | Historical checker evidence |
| `analysis/` | Flash/Pro/Kimi rule extraction and reflection analyses | Historical derived analysis |
| `datasets/historical/` | Pilot and Checker snapshots not used by the formal Online run | Immutable reproduction inputs |
| `tests/` | Smoke, prompt-fix, early integration outputs, the July 21 Offline interactive-checker smoke, and locally mirrored PolyBench PCE smoke3/4 evidence | No quality claim |
| `operations/` | Watchdog, preheat and local run logs, frozen Python-199 image-preparation evidence, and PolyBench `ulhpc-submit` submission logs | Operational evidence only |
| `failed-or-invalid/` | Runs with known invalid scores or unusable terminal state | Never use for scoring |

Archive policy:

- Move whole run directories; do not split checkpoints from their manifests.
- Preserve original-path mappings in `../catalog.json`.
- Historical valid and invalid runs remain separate.
- Archived configs may be used as examples, but a reproduction must use a new
  run directory and must not overwrite archived evidence.
- A run returns to the active surface only through an explicit methodology
  decision, not because a script happens to reference its old path.
