# Archived Experiment Configs

Archived configs are preserved for provenance and one-off reproduction. They
are not current defaults. Before executing one, review its dataset, run
directory, model budget, historical failure context, and referenced files.

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

The top-level `configs/README.md` separates active Behavioral acquisition,
retained Offline/frozen PolyBench foundations, and historical archives.
