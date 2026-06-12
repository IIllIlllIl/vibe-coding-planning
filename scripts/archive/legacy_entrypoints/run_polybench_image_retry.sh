#!/usr/bin/env bash
# Archived wrapper for the four historical Buster image retries.
# are recoverable with the Debian archive compatibility fallback.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

CONFIG="configs/polybench_remaining133_pct.yaml"
INSTANCES="configs/polybench_retry_images_buster4.json"
BATCH_ID="polybench-retry-images-buster4"

if [[ "${1:-}" == "--execute" ]]; then
  shift
  exec bash scripts/run_batch.sh \
    --config "$CONFIG" \
    --instances "$INSTANCES" \
    --batch-id "$BATCH_ID" \
    "$@"
fi

if [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--execute [run_batch options]]" >&2
  exit 2
fi

echo "Planning only. Pass --execute to start the four-instance rerun."
exec bash scripts/run_batch.sh \
  --dry-run \
  --config "$CONFIG" \
  --instances "$INSTANCES" \
  --batch-id "$BATCH_ID"
