#!/usr/bin/env bash
# Archived wrapper. Use scripts/run_batch.sh --checker-recovery.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

exec bash scripts/archive/legacy_entrypoints/run_checker_comparison.sh \
  "$@" --recovery
