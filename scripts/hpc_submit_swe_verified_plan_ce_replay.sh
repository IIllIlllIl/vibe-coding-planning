#!/usr/bin/env bash
# Reuse the mature SWE-Verified PCE transport with the recovered-Plan controller.
set -euo pipefail
export VIBE_PCE_RUNNER_SCRIPT=scripts/run_swe_verified_plan_ce_replay_hpc.py
export VIBE_PCE_SUBMIT_LABEL=swe-verified-plan-ce-replay-submit
exec bash "$(dirname "$0")/hpc_submit_swe_verified_pce.sh" "$@"
