# Archived Experiment Configs

Archived configs are preserved for provenance and one-off reproduction. They
are not current defaults. Before executing one, review its dataset, run
directory, model budget, historical failure context, and referenced files.

- `codex_pce_research/`: superseded script-owned Codex prototype, which had
  smoke only; preserved source/evidence references distinguish it from the
  maintained autoresearch adaptation.
- `supervisor_launches/`: completed, superseded and paused operational launch
  identities. Their contents are frozen reproduction records, not templates.
- `online_tests/`: dated Online GEPA smoke, resource, and resume runs.
- `online_gepa/`: formerly top-level Online GEPA formal, pilot, and supervisor configs.
- `offline_gepa/`: paused offline GEPA pilots and runtime variants.
- `behavioral_gepa/`: superseded Behavioral v1 smoke/formal configs and
  supervisor identities retained for audit.
- `pct_runs/`: paused PCT continuation, completion, and retry runs.
- `legacy_analysis/`: superseded Kimi/OpenCode analysis configuration.
- `polybench_pce/`: superseded PCE smoke, dependency-cache smoke, and
  repository-boundary configs.
- `polybench_pcce/`: superseded formal PCCE runtimes and supervisor identities
  retained for diagnostic provenance only.
- `polybench_preheat/`: superseded PolyBench dependency-preheat and preheat
  smoke configs.
- `pro/`: legacy SWE-bench Pro instance inputs without active Behavioral,
  Offline, or PolyBench reachability.
- `gepa_legacy/`: superseded offline GEPA definitions.

The top-level `configs/README.md` separates reviewed next-stage bindings,
frozen scientific contracts and historical archives. Files moved here retain
their original bytes and paths inside their contents. Reproduce those paths
from their original source revision rather than silently rewriting an old run.
