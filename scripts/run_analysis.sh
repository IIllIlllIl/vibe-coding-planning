#!/usr/bin/env bash
# Analysis runner for contrastive rule extraction.
#
# Serially processes reflect-success cases, skipping any that already have
# output. Writes a master log suitable for the long-run watchdog.
#
# Usage:
#   bash scripts/run_analysis.sh --model deepseek-v4-flash --output-dir ./output/analysis_flash
#   bash scripts/run_analysis.sh --model deepseek-v4-pro   --output-dir ./output/analysis_pro
#   bash scripts/run_analysis.sh --config configs/analysis_kimi_opencode.yaml --output-dir ./output/analysis_kimi_opencode_60

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Defaults
CONFIG="config.yaml"
MODEL_OVERRIDE=""
OUTPUT_DIR="./output/analysis_run"
INPUT_DIR="./output/SWE-bench_Verified/reflect_success_cases"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --model)
      MODEL_OVERRIDE="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --input-dir)
      INPUT_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

mkdir -p "$OUTPUT_DIR"
MASTER_LOG="logs/analysis_run.log"
mkdir -p "$(dirname "$MASTER_LOG")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Analysis start === config=$CONFIG model_override=${MODEL_OVERRIDE:-<config>} output=$OUTPUT_DIR" | tee -a "$MASTER_LOG"

# Read instance IDs from manifest.json
MANIFEST="$INPUT_DIR/manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: manifest.json not found at $MANIFEST" | tee -a "$MASTER_LOG"
  exit 1
fi

# Extract instance IDs using Python (more reliable than jq)
INSTANCE_IDS=$(python3 -c "
import json, sys
with open('$MANIFEST') as f:
    data = json.load(f)
for c in data.get('cases', []):
    print(c['instance_id'])
")

TOTAL=$(echo "$INSTANCE_IDS" | wc -l | tr -d ' ')
COMPLETED=0

for INSTANCE_ID in $INSTANCE_IDS; do
  # Idempotency: skip only if a valid per-case rule already exists.
  # Failed placeholder files must not block later breakpoint reruns.
  if [[ -f "$OUTPUT_DIR/per_case/${INSTANCE_ID}.json" ]]; then
    if python3 - "$OUTPUT_DIR/per_case/${INSTANCE_ID}.json" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(1)

rule = str(data.get("rule", "")).strip()
if data.get("rule_valid") is True and rule:
    sys.exit(0)
sys.exit(1)
PY
    then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] SKIP $INSTANCE_ID (valid result exists)" | tee -a "$MASTER_LOG"
      COMPLETED=$((COMPLETED + 1))
      continue
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] RETRY $INSTANCE_ID (existing result invalid)" | tee -a "$MASTER_LOG"
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] START $INSTANCE_ID" | tee -a "$MASTER_LOG"

  START_TIME=$(date +%s)

  CMD=(python -m src.analysis
    --config "$CONFIG"
    --input "$INPUT_DIR"
    --output "$OUTPUT_DIR"
    --instance "$INSTANCE_ID")
  if [[ -n "$MODEL_OVERRIDE" ]]; then
    CMD+=(--model "$MODEL_OVERRIDE")
  fi

  if "${CMD[@]}" 2>&1 | tee -a "$MASTER_LOG"; then
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] DONE  $INSTANCE_ID rc=0 elapsed=${ELAPSED}s" | tee -a "$MASTER_LOG"
    COMPLETED=$((COMPLETED + 1))
  else
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FAIL  $INSTANCE_ID rc=$? elapsed=${ELAPSED}s" | tee -a "$MASTER_LOG"
    # Continue to next instance — don't abort the whole batch
  fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Analysis end === completed=$COMPLETED/$TOTAL" | tee -a "$MASTER_LOG"
