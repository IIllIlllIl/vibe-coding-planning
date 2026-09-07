#!/usr/bin/env bash
# Pro-specific stable entry point; transport remains the shared PCCE submitter.
set -euo pipefail

exec bash "$(dirname "$0")/hpc_submit_swe_verified_pcce.sh" "$@"
