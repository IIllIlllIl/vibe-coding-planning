# Script Entry Points

## Active Behavioral Source Acquisition

- `tools/freeze_swe_chat_preheat_inputs.py` freezes the complete fixed-revision
  Hub file manifest and the ordered repository request manifest. Formal preheat
  consumes those JSON files rather than reinterpreting Parquet.
- `tools/login_swe_chat_preheat.py` downloads and verifies the dataset and Git
  mirrors on Iris with a single-writer lock, resumable state, atomic promotion,
  bounded dataset retries, and first-pass repository skip-and-report behavior.
- `tools/login_swe_chat_repository_recovery.py` consumes the frozen two-repo
  recovery manifest, uses the private Iris GitHub token through temporary
  AskPass, and writes an independent verified recovery overlay.
- `swe_chat_preheat_service.py` runs that bounded login preheater through local
  `tmux + caffeinate`, dispatching either acquisition config. It submits no
  Slurm job and invokes no Agent or LLM.
- `tools/build_swe_chat_stage1_selection.py` deterministically selects whole
  high-agent-authorship trajectories with at least one structured non-empty
  Plan, preserving disjoint recovery pools without reading behavioral results.
- `tools/build_swe_chat_stage2_slices.py` builds one first-Plan case per Stage-1
  trajectory from full raw transcripts, excludes assistant thinking from both
  projections, and separates decision-time context from behavioral evidence.
- `tools/audit_swe_chat_repository_reconstruction.py` tests the frozen
  canonical-checkpoint-parent hypothesis against pre-P1 `Read` evidence and
  conservatively audits structured `Write`/`Edit` replayability. It never runs
  transcript shell commands or delegated agents.
- `tools/build_swe_chat_temporal_repository_proxies.py` selects one explicitly
  approximate source commit strictly before each repository-ready session. It
  excludes Entire-managed refs and known current-session commits/descendants,
  and reads no behavior label or post-P1 evidence.
- `tools/build_swe_chat_behavioral_gepa_snapshot.py` joins frozen Stage-2 case
  files, repository-availability cleaning, temporal proxies, and a separately
  frozen complete split into the strict Behavioral Checker/supervision/
  Reflection/audit snapshot. It cannot choose or infer the split.

Source-acquisition authority is `docs/swe-chat-preheat.md`; Stage-1 and Stage-2
cleaning authority is `docs/swe-chat-data-cleaning.md`.

## Retained Offline And PolyBench Infrastructure

- `hpc_submit_batch.sh`, `hpc_resume_loop.py`, and
  `hpc_supervisor_service.py` are shared controller/supervisor infrastructure
  retained for Offline reproducibility.
- `internal/run_gepa_rules.py` is the retained Offline GEPA controller entry.
- `run_offline_check_only.py` is the additive fixed-guideline evaluation path.
- `hpc_submit_polybench_pce.sh`, `run_polybench_pce_hpc.py`,
  `hpc_submit_polybench_pcce.sh`, and `run_polybench_pcce_hpc.py` preserve the
  completed PCE/PCCE platform. The PCCE entry points also support the additive
  config-declared `checker_only` mode, which schedules exactly one PC wave and
  emits classification reports without Planner, Code, or Evaluate phases.
- `hpc_run_status.py` is the read-only status entry for retained persistent
  workflows.
- `tools/freeze_swe_verified_pce_source.py` freezes the fixed-revision complete
  SWE-Verified source rows; `tools/freeze_swe_verified_sif_manifest.py` audits
  selected existing SIF bytes and verifies each official base commit.
- `hpc_submit_swe_verified_pce.sh` / `run_swe_verified_pce_hpc.py` and
  `hpc_submit_swe_verified_pcce.sh` / `run_swe_verified_pcce_hpc.py` are the
  additive current-prompt SWE-Verified evaluation entry points. The PCE smoke
  reuses `hpc_resume_loop.py` and `hpc_supervisor_service.py` through its
  bounded launch config; tracked configs are not authorization by themselves.
- SIF and dependency preheaters remain because frozen PolyBench evidence and
  evaluator reproduction still reach them. They are not part of SWE-chat
  acquisition.

Completed workflows are not authorized launch defaults. Do not infer a new run
from the presence of an executable script.

## Historical Archive

- `archive/online_gepa/` contains the former Online resource-pilot preparation,
  worker, and submit scripts.
- `archive/legacy_entrypoints/` contains superseded checker, retry, migration,
  and OpenCode entrypoints.
- `archive/docker_cleanup_daemon/` contains the retired cleanup daemon.

`run_batch.sh` and `long_run_watchdog.py` remain temporarily at top level
because they mix historical behavior with references still reached by retained
Offline tests. They are not current entrypoints and will be considered only in
the later import/reachability cleanup.

All Python commands use `conda run -n mini-swe`. No LLM, GEPA, Docker,
Apptainer, HPC, PCE, or PCCE operation starts without explicit authorization.
