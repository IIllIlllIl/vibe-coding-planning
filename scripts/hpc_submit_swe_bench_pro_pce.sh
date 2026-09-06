#!/usr/bin/env bash
# Reuse the audited SWE PCE staging/controller wrapper with Pro identities.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export VIBE_PCE_CONFIG_MODE=swe_bench_pro_pce
export VIBE_PCE_RUNNER_SCRIPT=scripts/run_swe_bench_pro_pce_hpc.py
export VIBE_PCE_SUBMIT_LABEL=swe-bench-pro-pce-submit

exec bash "$ROOT/scripts/hpc_submit_swe_verified_pce.sh" \
  --job-name swe-bench-pro-pce-controller \
  --remote-dir '~/hpc_runs/vibe-swe-bench-pro-pce' \
  "$@"
