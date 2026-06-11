#!/usr/bin/env bash
# Resume only failed or incomplete checker arms in baseline-first order.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

exec bash scripts/run_checker_comparison.sh "$@" --recovery
