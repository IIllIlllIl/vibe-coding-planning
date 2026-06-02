#!/usr/bin/env bash
# Unified batch runner for SWE-bench and PolyBench instances.
#
# Auto-detects dataset from config.yaml (system.dataset), derives instance
# source and output paths automatically.
#
# Usage:
#   bash scripts/run_batch.sh           # real run
#   bash scripts/run_batch.sh --dry-run # list SKIP/RUN, no pipeline calls
#   bash scripts/run_batch.sh --instances FILE  # override instance list
#
# Outputs:
#   logs/batch_run.log         - master log (status + duration per instance)
#   logs/batch/<instance>.log  - per-instance stdout+stderr
set -uo pipefail

DRY_RUN=0
INSTANCE_FILE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --instances)
      INSTANCE_FILE_OVERRIDE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MASTER_LOG="logs/batch_run.log"
PER_INSTANCE_LOG_DIR="logs/batch"
mkdir -p "$PER_INSTANCE_LOG_DIR"

# ---------------------------------------------------------------------------
# Activate conda env
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

python -c "import minisweagent, swebench" 2>/dev/null \
  || { echo "ERROR: minisweagent/swebench not importable in active env" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Read batch config from config.yaml
# ---------------------------------------------------------------------------
read -r BATCH_ID DATASET DATASET_SHORT \
  < <(python -c "
import sys, yaml
try:
    cfg = yaml.safe_load(open('config.yaml'))
    bid = (cfg.get('system') or {}).get('batch_id') or ''
    bid = bid.strip()
    if not bid:
        sys.stderr.write('ERROR: system.batch_id is empty in config.yaml\n')
        sys.exit(2)
    dataset = (cfg.get('system') or {}).get('dataset', 'SWE-bench/SWE-bench_Verified')
    dataset_short = dataset.split('/')[-1]
    print(bid, dataset, dataset_short)
except Exception as e:
    sys.stderr.write(f'ERROR: failed to read config: {e}\n')
    sys.exit(2)
") || exit 1

# ---------------------------------------------------------------------------
# Determine instance list file
# ---------------------------------------------------------------------------
if [[ -n "$INSTANCE_FILE_OVERRIDE" ]]; then
  INSTANCE_FILE="$INSTANCE_FILE_OVERRIDE"
else
  # Verified: batch-scoped sample file
  if [[ "$DATASET" == *"Verified"* ]]; then
    INSTANCE_FILE="output/$DATASET_SHORT/$BATCH_ID/sampled_instances.json"
  # Pro: use ansible-only subset (Mac ARM compatible), fallback to python
  elif [[ "$DATASET" == *"Pro"* ]]; then
    if [[ -f "pro_ansible_instances.json" ]]; then
      INSTANCE_FILE="pro_ansible_instances.json"
    elif [[ -f "pro_python_instances.json" ]]; then
      INSTANCE_FILE="pro_python_instances.json"
    else
      echo "ERROR: No Pro instance list found (tried pro_ansible_instances.json, pro_python_instances.json)" >&2
      exit 1
    fi
  # Other configured datasets (e.g. PolyBench): prefer a batch-scoped
  # manifest; otherwise materialize system.instances from config.yaml.
  else
    INSTANCE_FILE="output/$DATASET_SHORT/$BATCH_ID/sampled_instances.json"
    if [[ ! -f "$INSTANCE_FILE" ]]; then
      mkdir -p "$(dirname "$INSTANCE_FILE")"
      python -c "
import json, sys, yaml
cfg = yaml.safe_load(open('config.yaml')) or {}
instances = ((cfg.get('system') or {}).get('instances') or [])
if not instances:
    sys.stderr.write('ERROR: system.instances is empty and no batch manifest exists\n')
    sys.exit(2)
with open('$INSTANCE_FILE', 'w', encoding='utf-8') as f:
    json.dump({'instances': instances}, f, indent=2)
" || exit 1
    fi
  fi
fi

if [[ ! -f "$INSTANCE_FILE" ]]; then
  echo "ERROR: instance file not found: $INSTANCE_FILE" >&2
  exit 1
fi

# DEEPSEEK_API_KEY only matters for real runs
if [[ $DRY_RUN -eq 0 && -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "ERROR: DEEPSEEK_API_KEY not set" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Read instance IDs (bash 3.2 compatible, handles both array and object formats)
# ---------------------------------------------------------------------------
INSTANCES=()
while IFS= read -r line; do
  [[ -n "$line" ]] && INSTANCES+=("$line")
done < <(python -c "
import json
with open('$INSTANCE_FILE') as f:
    data = json.load(f)
    # Handle {'instances': [...]} (Verified format) or [...] (Pro format)
    if isinstance(data, dict):
        ids = data.get('instances', [])
    elif isinstance(data, list):
        ids = data
    else:
        ids = []
    for x in ids:
        print(x)
")

TOTAL=${#INSTANCES[@]}
if [[ $TOTAL -eq 0 ]]; then
  echo "ERROR: instance list is empty (parse failure?) from $INSTANCE_FILE" >&2
  exit 1
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[DRY-RUN] $TOTAL instances loaded from $INSTANCE_FILE"
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch start: dataset=$DATASET batch_id=$BATCH_ID $TOTAL instances (dry_run=$DRY_RUN) ===" \
  | tee -a "$MASTER_LOG"

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
i=0
for INSTANCE in "${INSTANCES[@]}"; do
  i=$((i + 1))
  RESULT_FILE="output/$DATASET_SHORT/$BATCH_ID/$INSTANCE/result.json"

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
