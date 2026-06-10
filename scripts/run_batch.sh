#!/usr/bin/env bash
# Unified batch runner for SWE-bench and PolyBench instances.
#
# Auto-detects dataset from the selected config (system.dataset), derives instance
# source and output paths automatically.
#
# Usage:
#   bash scripts/run_batch.sh           # real run
#   bash scripts/run_batch.sh --config configs/polybench_full199_pct.yaml
#   bash scripts/run_batch.sh --dry-run # list SKIP/RUN, no pipeline calls
#   bash scripts/run_batch.sh --instances FILE  # override instance list
#   bash scripts/run_batch.sh --run-analysis --analysis-model MODEL --analysis-output-dir DIR
#   bash scripts/run_batch.sh --analysis-only --analysis-config configs/analysis_kimi_opencode.yaml
#
# Outputs:
#   logs/batch_run.log         - master log (status + duration per instance)
#   logs/batch/<instance>.log  - per-instance stdout+stderr
set -uo pipefail

DRY_RUN=0
CONFIG="config.yaml"
INSTANCE_FILE_OVERRIDE=""
BATCH_ID_OVERRIDE=""
RUN_ANALYSIS=0
ANALYSIS_ONLY=0
ANALYSIS_ALLOW_FAILURES=0
ANALYSIS_CONFIG=""
ANALYSIS_MODEL_OVERRIDE=""
ANALYSIS_OUTPUT_DIR_OVERRIDE=""
ANALYSIS_INPUT_DIR_OVERRIDE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --instances)
      INSTANCE_FILE_OVERRIDE="$2"
      shift 2
      ;;
    --batch-id)
      BATCH_ID_OVERRIDE="$2"
      shift 2
      ;;
    --run-analysis)
      RUN_ANALYSIS=1
      shift
      ;;
    --analysis-only)
      RUN_ANALYSIS=1
      ANALYSIS_ONLY=1
      shift
      ;;
    --analysis-allow-failures)
      ANALYSIS_ALLOW_FAILURES=1
      shift
      ;;
    --analysis-config)
      ANALYSIS_CONFIG="$2"
      shift 2
      ;;
    --analysis-model)
      ANALYSIS_MODEL_OVERRIDE="$2"
      shift 2
      ;;
    --analysis-output-dir)
      ANALYSIS_OUTPUT_DIR_OVERRIDE="$2"
      shift 2
      ;;
    --analysis-input-dir)
      ANALYSIS_INPUT_DIR_OVERRIDE="$2"
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

CAFFEINATE_CMD=()
if command -v caffeinate >/dev/null 2>&1; then
  CAFFEINATE_CMD=(caffeinate -i -s -d)
fi

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
# Read batch config from the selected config file
# ---------------------------------------------------------------------------
read -r BATCH_ID DATASET DATASET_SHORT DOCKER_MIN_FREE_GB DOCKER_DELETE_IMAGES DOCKER_MAX_CACHED_IMAGES ANALYSIS_MODEL CONFIG_ANALYSIS_OUTPUT_DIR \
  < <(python -c "
import sys, yaml
try:
    config_path = sys.argv[1]
    cfg = yaml.safe_load(open(config_path))
    system = cfg.get('system') or {}
    docker = cfg.get('docker') or {}
    bid = system.get('batch_id') or ''
    bid = bid.strip()
    if not bid:
        sys.stderr.write(f'ERROR: system.batch_id is empty in {config_path}\n')
        sys.exit(2)
    dataset = system.get('dataset', 'SWE-bench/SWE-bench_Verified')
    dataset_short = dataset.split('/')[-1]
    min_free = int(docker.get('min_free_gb', 20))
    delete_images = str(docker.get('delete_images_after_instance', True)).lower()
    max_cached = int(docker.get('max_cached_images', 75))
    analysis = cfg.get('analysis') or {}
    analysis_model = analysis.get('model', 'deepseek-v4-flash')
    analysis_output = analysis.get('output_dir', './output/analysis_results')
    print(bid, dataset, dataset_short, min_free, delete_images, max_cached, analysis_model, analysis_output)
except Exception as e:
    sys.stderr.write(f'ERROR: failed to read config: {e}\n')
    sys.exit(2)
" "$CONFIG") || exit 1

if [[ -z "$ANALYSIS_CONFIG" ]]; then
  ANALYSIS_CONFIG="$CONFIG"
fi
if [[ -n "$BATCH_ID_OVERRIDE" ]]; then
  if [[ ! "$BATCH_ID_OVERRIDE" =~ ^[A-Za-z0-9_.-]+$ || "$BATCH_ID_OVERRIDE" == "." || "$BATCH_ID_OVERRIDE" == ".." ]]; then
    echo "ERROR: invalid --batch-id: $BATCH_ID_OVERRIDE" >&2
    exit 1
  fi
  BATCH_ID="$BATCH_ID_OVERRIDE"
fi

if [[ -n "$ANALYSIS_MODEL_OVERRIDE" ]]; then
  ANALYSIS_MODEL="$ANALYSIS_MODEL_OVERRIDE"
fi
if [[ -n "$ANALYSIS_OUTPUT_DIR_OVERRIDE" ]]; then
  CONFIG_ANALYSIS_OUTPUT_DIR="$ANALYSIS_OUTPUT_DIR_OVERRIDE"
fi
if [[ "$ANALYSIS_CONFIG" != "config.yaml" ]]; then
  read -r CONFIG_ANALYSIS_MODEL_FROM_FILE CONFIG_ANALYSIS_OUTPUT_DIR_FROM_FILE < <(python -c "
import sys, yaml
try:
    cfg = yaml.safe_load(open('$ANALYSIS_CONFIG')) or {}
    analysis = cfg.get('analysis') or {}
    print(analysis.get('model', 'deepseek-v4-flash'), analysis.get('output_dir', './output/analysis_results'))
except Exception as e:
    sys.stderr.write(f'ERROR: failed to read analysis config: {e}\n')
    sys.exit(2)
") || exit 1
  if [[ -z "$ANALYSIS_MODEL_OVERRIDE" ]]; then
    ANALYSIS_MODEL="$CONFIG_ANALYSIS_MODEL_FROM_FILE"
  fi
  if [[ -z "$ANALYSIS_OUTPUT_DIR_OVERRIDE" ]]; then
    CONFIG_ANALYSIS_OUTPUT_DIR="$CONFIG_ANALYSIS_OUTPUT_DIR_FROM_FILE"
  fi
fi
ANALYSIS_INPUT_DIR="${ANALYSIS_INPUT_DIR_OVERRIDE:-./output/SWE-bench_Verified/reflect_success_cases}"

if [[ "$DATASET" == *"PolyBench"* ]]; then
  python -c "from poly_bench_evaluation.docker_utils import DockerManager; from poly_bench_evaluation.repo_utils import RepoManager" 2>/dev/null \
    || {
      echo "ERROR: official PolyBench evaluator submodules are unavailable." >&2
      echo "Install SWE-PolyBench from a persistent checkout; do not use an editable /tmp checkout." >&2
      exit 1
    }
fi

free_gb() {
  df -Pk . | awk 'NR==2 {print int($4 / 1024 / 1024)}'
}

check_free_space() {
  local free
  free="$(free_gb)"
  if [[ "$free" -lt "$DOCKER_MIN_FREE_GB" ]]; then
    if [[ "$DOCKER_DELETE_IMAGES" == "true" ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Low disk space before instance: ${free}GiB free; attempting Docker cache cleanup" \
        | tee -a "$MASTER_LOG"
      cleanup_docker_after_instance
      free="$(free_gb)"
    fi
  fi
  if [[ "$free" -lt "$DOCKER_MIN_FREE_GB" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FATAL low disk space: ${free}GiB free < ${DOCKER_MIN_FREE_GB}GiB threshold" \
      | tee -a "$MASTER_LOG"
    return 1
  fi
  return 0
}

cleanup_docker_after_instance() {
  if [[ "$DOCKER_DELETE_IMAGES" != "true" ]]; then
    return 0
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Docker cleanup: retaining newest ${DOCKER_MAX_CACHED_IMAGES} project images" \
    | tee -a "$MASTER_LOG"
  python -m src.environment.docker_env maintain \
    --max-cached-images "$DOCKER_MAX_CACHED_IMAGES" \
    >> "$MASTER_LOG" 2>&1 || true
}

is_docker_storage_error() {
  local log_file="$1"
  grep -Eiq 'no space left on device|input/output error|containerd\.metadata|meta\.db|/var/lib/desktop-containerd' "$log_file"
}

# ---------------------------------------------------------------------------
# Determine instance list file
# ---------------------------------------------------------------------------
INSTANCES=()
TOTAL=0
if [[ $ANALYSIS_ONLY -eq 0 ]]; then
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
cfg = yaml.safe_load(open(sys.argv[1])) or {}
instances = ((cfg.get('system') or {}).get('instances') or [])
if not instances:
    sys.stderr.write('ERROR: system.instances is empty and no batch manifest exists\n')
    sys.exit(2)
with open(sys.argv[2], 'w', encoding='utf-8') as f:
    json.dump({'instances': instances}, f, indent=2)
" "$CONFIG" "$INSTANCE_FILE" || exit 1
      fi
    fi
  fi

  if [[ ! -f "$INSTANCE_FILE" ]]; then
    echo "ERROR: instance file not found: $INSTANCE_FILE" >&2
    exit 1
  fi
fi

# DEEPSEEK_API_KEY only matters for real runs
if [[ $DRY_RUN -eq 0 && -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "ERROR: DEEPSEEK_API_KEY not set" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Read instance IDs (bash 3.2 compatible, handles both array and object formats)
# ---------------------------------------------------------------------------
if [[ $ANALYSIS_ONLY -eq 0 ]]; then
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
fi

if [[ $DRY_RUN -eq 1 ]]; then
  if [[ $ANALYSIS_ONLY -eq 1 ]]; then
    echo "[DRY-RUN] batch skipped because analysis_only=1"
  else
    echo "[DRY-RUN] $TOTAL instances loaded from $INSTANCE_FILE"
  fi
  if [[ $RUN_ANALYSIS -eq 1 ]]; then
    echo "[DRY-RUN] analysis would run: config=$ANALYSIS_CONFIG model=$ANALYSIS_MODEL input=$ANALYSIS_INPUT_DIR output=$CONFIG_ANALYSIS_OUTPUT_DIR analysis_only=$ANALYSIS_ONLY"
  fi
fi
echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch start: dataset=$DATASET batch_id=$BATCH_ID $TOTAL instances (dry_run=$DRY_RUN) ===" \
  | tee -a "$MASTER_LOG"

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
i=0
FAIL_COUNT=0
if [[ $ANALYSIS_ONLY -eq 0 ]]; then
  for INSTANCE in "${INSTANCES[@]}"; do
    i=$((i + 1))
    RESULT_FILE="output/$DATASET_SHORT/$BATCH_ID/$INSTANCE/result.json"

    check_free_space || exit 75

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

    "${CAFFEINATE_CMD[@]}" python -m src.main --instance "$INSTANCE" --config "$CONFIG" --batch-id "$BATCH_ID" \
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
      FAIL_COUNT=$((FAIL_COUNT + 1))
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$i/$TOTAL] FAIL  $INSTANCE rc=$RC elapsed=${ELAPSED}s (see $PER_LOG)" \
        | tee -a "$MASTER_LOG"
      if is_docker_storage_error "$PER_LOG"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] FATAL Docker storage error detected; stopping batch before more instances fail" \
          | tee -a "$MASTER_LOG"
        exit 74
      fi
    fi

    cleanup_docker_after_instance
  done

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch end ===" | tee -a "$MASTER_LOG"
else
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Batch skipped: analysis_only=1 ===" | tee -a "$MASTER_LOG"
fi

if [[ $RUN_ANALYSIS -eq 1 && $DRY_RUN -eq 0 ]]; then
  if [[ $FAIL_COUNT -gt 0 && $ANALYSIS_ALLOW_FAILURES -ne 1 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Analysis skipped: $FAIL_COUNT instance(s) failed; pass --analysis-allow-failures to run anyway" \
      | tee -a "$MASTER_LOG"
    exit 0
  fi

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Analysis handoff start: config=$ANALYSIS_CONFIG model=$ANALYSIS_MODEL input=$ANALYSIS_INPUT_DIR output=$CONFIG_ANALYSIS_OUTPUT_DIR ===" \
    | tee -a "$MASTER_LOG"
  ANALYSIS_CMD=(bash scripts/run_analysis.sh
    --config "$ANALYSIS_CONFIG"
    --input-dir "$ANALYSIS_INPUT_DIR"
    --output-dir "$CONFIG_ANALYSIS_OUTPUT_DIR")
  if [[ -n "$ANALYSIS_MODEL_OVERRIDE" ]]; then
    ANALYSIS_CMD+=(--model "$ANALYSIS_MODEL")
  fi
  if "${ANALYSIS_CMD[@]}"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Analysis handoff end: rc=0 ===" \
      | tee -a "$MASTER_LOG"
  else
    RC=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] === Analysis handoff failed: rc=$RC ===" \
      | tee -a "$MASTER_LOG"
    exit "$RC"
  fi
fi
