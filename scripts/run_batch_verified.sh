#!/usr/bin/env bash
# Sequentially run sampled SWE-bench Verified instances.
#
# Reads system.batch_id from config.yaml, then reads instance IDs from
# output/SWE-bench_Verified/<batch_id>/sampled_instances.json. Skips any
# whose result.json already exists (idempotent / resumable across manual
# restarts), and runs the rest one at a time using config.yaml's n.
#
# Bash 3.2 compatible (macOS /bin/bash). Tested patterns:
#   * No `mapfile` / `readarray` (bash 4+ only).
#   * No `${ARR[@]}` on possibly-empty arrays under `set -u`.
#
# Usage:
#   bash scripts/run_batch_verified.sh           # real run
#   bash scripts/run_batch_verified.sh --dry-run # list SKIP/RUN, no pipeline calls
#
# Outputs:
#   logs/batch_run.log         - master log (status + duration per instance)
#   logs/batch/<instance>.log  - per-instance stdout+stderr
set -uo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MASTER_LOG="logs/batch_run.log"
PER_INSTANCE_LOG_DIR="logs/batch"
mkdir -p "$PER_INSTANCE_LOG_DIR"

# ---------------------------------------------------------------------------
# Activate conda env. Always activate (dry-run also validates this path so
# we know it will work for the real run). Hardcoded base path verified
# against `conda info --base` on this machine (/Users/taoran.wang/miniconda3);
# fail loudly if the layout has changed.
# ---------------------------------------------------------------------------
CONDA_BASE="/Users/taoran.wang/miniconda3"
CONDA_HOOK="$CONDA_BASE/etc/profile.d/conda.sh"

if [[ ! -f "$CONDA_HOOK" ]]; then
  echo "ERROR: conda hook not found at $CONDA_HOOK" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$CONDA_HOOK"
conda activate mini-swe || { echo "ERROR: failed to activate conda env mini-swe" >&2; exit 1; }

# Sanity: required packages must be importable
python -c "import minisweagent, swebench" 2>/dev/null \
  || { echo "ERROR: minisweagent/swebench not importable in active env" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Read batch_id from config.yaml. Results are scoped per-batch:
#   output/SWE-bench_Verified/<BATCH_ID>/<INSTANCE>/result.json
# The sample file (list of instances for this batch) is also batch-scoped so
# re-running with a new batch_id naturally targets a different set without
# editing this script. Falls back to 'default' is NOT done — the loader
# requires batch_id to be non-empty (FatalError otherwise), so we fail fast
# here too.
# ---------------------------------------------------------------------------
BATCH_ID=$(python -c "
import sys, yaml
try:
    cfg = yaml.safe_load(open('config.yaml'))
    bid = (cfg.get('system') or {}).get('batch_id') or ''
    bid = bid.strip()
    if not bid:
        sys.stderr.write('ERROR: system.batch_id is empty in config.yaml\n')
        sys.exit(2)
    print(bid)
except Exception as e:
    sys.stderr.write(f'ERROR: failed to read system.batch_id from config.yaml: {e}\n')
    sys.exit(2)
") || exit 1

SAMPLE_FILE="output/SWE-bench_Verified/$BATCH_ID/sampled_instances.json"
if [[ ! -f "$SAMPLE_FILE" ]]; then
  echo "ERROR: sample file not found: $SAMPLE_FILE" >&2
  echo "       (expected manifest for batch_id=$BATCH_ID)" >&2
  exit 1
fi

# DEEPSEEK_API_KEY only matters for real runs (pipeline calls the LLM).
if [[ $DRY_RUN -eq 0 && -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "ERROR: DEEPSEEK_API_KEY not set" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Read instance IDs (bash 3.2 compatible: while-read into array).
# Use the ambient python (system or active conda) — only stdlib needed.
# ---------------------------------------------------------------------------
INSTANCES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && INSTANCES+=("$line")
done < <(python -c "
import json
with open('$SAMPLE_FILE') as f:
    for x in json.load(f)['instances']:
        print(x)
")

TOTAL=${#INSTANCES[@]}
if [[ $TOTAL -eq 0 ]]; then
  echo "ERROR: instance list is empty (parse failure?)" >&2
  exit 1
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[DRY-RUN] $TOTAL instances loaded from $SAMPLE_FILE"
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch start: batch_id=$BATCH_ID  $TOTAL instances (dry_run=$DRY_RUN) ===" \
  | tee -a "$MASTER_LOG"

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
i=0
for INSTANCE in "${INSTANCES[@]}"; do
  i=$((i + 1))
  RESULT_FILE="output/SWE-bench_Verified/$BATCH_ID/$INSTANCE/result.json"

  if [[ -f "$RESULT_FILE" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] SKIP $INSTANCE (result.json exists)" \
      | tee -a "$MASTER_LOG"
    continue
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] WOULD-RUN $INSTANCE" \
      | tee -a "$MASTER_LOG"
    continue
  fi

  PER_LOG="$PER_INSTANCE_LOG_DIR/$INSTANCE.log"
  START_EPOCH=$(date +%s)
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] START $INSTANCE -> $PER_LOG" \
    | tee -a "$MASTER_LOG"

  # Run the pipeline. n is taken from config.yaml (currently 3); we do NOT
  # pass --n so the config remains the single source of truth.
  python -m src.main --instance "$INSTANCE" --config config.yaml \
    > "$PER_LOG" 2>&1
  RC=$?

  END_EPOCH=$(date +%s)
  ELAPSED=$((END_EPOCH - START_EPOCH))

  if [[ $RC -eq 0 && -f "$RESULT_FILE" ]]; then
    RESOLVED=$(python -c "
import json, sys
try:
    d = json.load(open('$RESULT_FILE'))
    plans = d.get('plans', [])
    print(any(p.get('test_results', {}).get('resolved') for p in plans))
except Exception as e:
    print('parse_error:' + str(e))
")
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] DONE  $INSTANCE rc=$RC elapsed=${ELAPSED}s resolved=$RESOLVED" \
      | tee -a "$MASTER_LOG"
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] FAIL  $INSTANCE rc=$RC elapsed=${ELAPSED}s (see $PER_LOG)" \
      | tee -a "$MASTER_LOG"
  fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch end ===" | tee -a "$MASTER_LOG"
