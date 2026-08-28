#!/usr/bin/env bash
# Submit an Apptainer SIF preheat job through ulhpc-submit.
#
# This wrapper intentionally does not hand-write Slurm/module setup. It reuses
# ulhpc-submit for sync, module loading, staging, Apptainer cache env, and
# submit-only JSON output. The actual image preheat logic lives in
# scripts/tools/prepare_apptainer_sifs.py.
set -euo pipefail

CONFIG=""
SIF_CACHE_DIR=""
JOB_NAME="vibe-preheat-sifs"
PARTITION="batch"
CPUS="1"
MEM="4G"
TIME="08:00:00"
TIMEOUT="0"
MAX_ATTEMPTS="1"
RETRY_BACKOFF="0"
REMOTE_DIR=""
REMOTE_DATASET_DIR="~/hpc_datasets/vibe-coding-planning"
REMOTE_APPTAINER_CACHE_DIR=""
REMOTE_APPTAINER_TMP_DIR=""
ULHPC_CONFIG=""
FULL_LOGS=0
SUBMIT=0
INSTALL_DEPS=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/tools/submit_apptainer_sif_preheat.sh --config PATH [options]

Required:
  --config PATH          GEPA config file, relative to repo root or absolute

Preheat options:
  --sif-cache-dir DIR    Shared remote SIF cache directory
                         (default: container.sif_cache_dir from config)
  --timeout SECONDS      Timeout per SIF pull attempt; 0 disables the per-pull timeout
                         (default: 0)
  --max-attempts N       Attempts per missing SIF image (default: 1)
  --retry-backoff SEC    Seconds between failed pull attempts (default: 0)

Slurm / ulhpc-submit options:
  --job-name NAME        Job name (default: vibe-preheat-sifs)
  --partition NAME       Slurm partition (default: batch)
  --cpus N               CPUs per task (default: 1)
  --mem SIZE             Memory (default: 4G)
  --time HH:MM:SS        Wall time (default: 08:00:00)
  --remote-dir DIR       Remote project workdir
  --remote-dataset-dir DIR
                         Remote dataset staging root outside --remote-dir
                         (default: ~/hpc_datasets/vibe-coding-planning)
  --remote-apptainer-cache-dir DIR
                         Remote APPTAINER_CACHEDIR
  --remote-apptainer-tmp-dir DIR
                         Remote APPTAINER_TMPDIR
  --ulhpc-config FILE    ulhpc-submit config file
                         (default: configs/ulhpc_submit.yaml if present)
  --full-logs            Ask ulhpc-submit to retrieve full logs when monitored
  --submit               Actually submit the job (default is dry-run)
  --install-deps         Run pip install --user -r requirements.txt before preheat
                         (default: skip dependency installation)

Examples:
  bash scripts/tools/submit_apptainer_sif_preheat.sh \
    --config configs/archive/online_gepa/gepa_online_planning_hpc.yaml \
    --sif-cache-dir "${VIBE_HPC_ROOT}/shared/sif-cache" \
    --time 08:00:00 \
    --submit
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --sif-cache-dir)
      SIF_CACHE_DIR="$2"
      shift 2
      ;;
    --job-name)
      JOB_NAME="$2"
      shift 2
      ;;
    --partition)
      PARTITION="$2"
      shift 2
      ;;
    --cpus)
      CPUS="$2"
      shift 2
      ;;
    --mem)
      MEM="$2"
      shift 2
      ;;
    --time)
      TIME="$2"
      shift 2
      ;;
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --max-attempts)
      MAX_ATTEMPTS="$2"
      shift 2
      ;;
    --retry-backoff)
      RETRY_BACKOFF="$2"
      shift 2
      ;;
    --remote-dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    --remote-dataset-dir)
      REMOTE_DATASET_DIR="$2"
      shift 2
      ;;
    --remote-apptainer-cache-dir)
      REMOTE_APPTAINER_CACHE_DIR="$2"
      shift 2
      ;;
    --remote-apptainer-tmp-dir)
      REMOTE_APPTAINER_TMP_DIR="$2"
      shift 2
      ;;
    --ulhpc-config)
      ULHPC_CONFIG="$2"
      shift 2
      ;;
    --full-logs)
      FULL_LOGS=1
      shift
      ;;
    --submit)
      SUBMIT=1
      shift
      ;;
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --dry-run)
      SUBMIT=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$CONFIG" ]]; then
  echo "ERROR: --config is required" >&2
  usage >&2
  exit 2
fi
if ! [[ "$CPUS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --cpus must be a positive integer" >&2
  exit 2
fi
if ! [[ "$TIMEOUT" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --timeout must be a non-negative integer" >&2
  exit 2
fi
if ! [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --max-attempts must be a positive integer" >&2
  exit 2
fi
if ! [[ "$RETRY_BACKOFF" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --retry-backoff must be a non-negative integer" >&2
  exit 2
fi
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_ABS="$(python -c "from pathlib import Path; print((Path('$REPO_ROOT') / '$CONFIG').resolve())")"
if [[ ! -f "$CONFIG_ABS" ]]; then
  echo "ERROR: config not found: $CONFIG" >&2
  exit 2
fi

if [[ -z "$ULHPC_CONFIG" && -f "$REPO_ROOT/configs/ulhpc_submit.yaml" ]]; then
  ULHPC_CONFIG="$REPO_ROOT/configs/ulhpc_submit.yaml"
fi

ULHPC_REMOTE_USER="$(python - "${ULHPC_CONFIG:-}" <<'PY'
import os
import sys
from pathlib import Path

import yaml

user = os.environ.get("ULHPC_USER", "")
if not user and len(sys.argv) > 1 and sys.argv[1]:
    path = Path(sys.argv[1])
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        user = str(data.get("user", "") or "")
if not user:
    user = os.environ.get("USER", "")
print(user)
PY
)"
if [[ -z "$ULHPC_REMOTE_USER" ]]; then
  echo "ERROR: cannot determine ULHPC remote user; set configs/ulhpc_submit.yaml user or ULHPC_USER" >&2
  exit 2
fi
if [[ -z "${ULHPC_USER:-}" ]]; then
  export ULHPC_USER="$ULHPC_REMOTE_USER"
fi
VIBE_HPC_ROOT="${VIBE_HPC_ROOT:-/scratch/users/${ULHPC_REMOTE_USER}/vibe-coding-planning}"
if [[ -z "$REMOTE_APPTAINER_CACHE_DIR" ]]; then
  REMOTE_APPTAINER_CACHE_DIR="${VIBE_HPC_ROOT}/shared/apptainer-cache"
fi
if [[ -z "$REMOTE_APPTAINER_TMP_DIR" ]]; then
  REMOTE_APPTAINER_TMP_DIR="${VIBE_HPC_ROOT}/shared/apptainer-tmp"
fi
for value_name in REMOTE_DATASET_DIR REMOTE_APPTAINER_CACHE_DIR REMOTE_APPTAINER_TMP_DIR; do
  if [[ -z "${!value_name}" ]]; then
    echo "ERROR: $value_name must not be empty" >&2
    exit 2
  fi
done

if ! command -v ulhpc-submit >/dev/null 2>&1; then
  echo "ERROR: ulhpc-submit not found. Install the adjacent hpc_submit project:" >&2
  echo "  cd ../../hpc_submit && pip install -e \".[dev]\"" >&2
  exit 127
fi

CONFIG_VALUES=$(python - "$CONFIG_ABS" "${ULHPC_CONFIG:-}" <<'PY'
import sys
from pathlib import Path

import yaml

gepa_config = Path(sys.argv[1])
ulhpc_config = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
repo_root = gepa_config.parents[1] if gepa_config.parent.name == "configs" else Path.cwd()
cfg = yaml.safe_load(gepa_config.read_text(encoding="utf-8")) or {}
paths = cfg.get("paths", {})

def resolve(raw: str) -> str:
    if not raw:
        return ""
    value = Path(raw)
    return str(value if value.is_absolute() else repo_root / value)

print(f"dataset_snapshot={resolve(paths.get('dataset_snapshot', ''))}")
container = cfg.get("container", {}) or {}
print(f"sif_cache_dir={container.get('sif_cache_dir', '')}")

ulhpc = {}
if ulhpc_config and ulhpc_config.exists():
    ulhpc = yaml.safe_load(ulhpc_config.read_text(encoding="utf-8")) or {}
print(f"python_module={ulhpc.get('python_module', '')}")
print(f"container_module={ulhpc.get('container_module', '')}")
PY
)

DATASET_SNAPSHOT=""
CONFIG_SIF_CACHE_DIR=""
CONFIG_PYTHON_MODULE=""
CONFIG_CONTAINER_MODULE=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    sif_cache_dir) CONFIG_SIF_CACHE_DIR="$VALUE" ;;
    python_module) CONFIG_PYTHON_MODULE="$VALUE" ;;
    container_module) CONFIG_CONTAINER_MODULE="$VALUE" ;;
  esac
done <<< "$CONFIG_VALUES"

if [[ -z "$DATASET_SNAPSHOT" || ! -d "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot directory not found locally: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ -z "$SIF_CACHE_DIR" ]]; then
  SIF_CACHE_DIR="$CONFIG_SIF_CACHE_DIR"
fi
if [[ -z "$SIF_CACHE_DIR" ]]; then
  echo "ERROR: --sif-cache-dir is required when GEPA config has no container.sif_cache_dir" >&2
  exit 2
fi

PYTHON_MODULE="${ULHPC_PYTHON_MODULE:-${CONFIG_PYTHON_MODULE:-lang/Python/3.11}}"
CONTAINER_MODULE="${ULHPC_CONTAINER_MODULE:-${CONFIG_CONTAINER_MODULE:-tools/Apptainer}}"
if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="~/hpc_runs/vibe-coding-planning-sif-preheat"
fi

DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
if [[ "$DATASET_REL" == "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot must be inside the repository for --link-as: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ "$CONFIG_REL" == "$CONFIG_ABS" ]]; then
  echo "ERROR: GEPA config must be inside the repository: $CONFIG_ABS" >&2
  exit 2
fi

REMOTE_DATASET_SNAPSHOT="$REMOTE_DATASET_DIR/$DATASET_REL"
if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  REMOTE_INSTALL_DEPS='python3 -m pip install --quiet --user -r requirements.txt'
else
  REMOTE_INSTALL_DEPS='echo "[sif-preheat] skipping dependency install; use --install-deps to enable"'
fi

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[sif-preheat] started at \$(date) on \$(hostname)"
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$SIF_CACHE_DIR"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR" "\$ULHPC_APPTAINER_SIF_CACHE_DIR"
test -f "$DATASET_REL/manifest.json" || {
  echo "[sif-preheat] dataset snapshot missing after staging: $DATASET_REL" >&2
  exit 2
}
$REMOTE_INSTALL_DEPS
python3 scripts/tools/prepare_apptainer_sifs.py \
  --config "$CONFIG_REL" \
  --sif-cache-dir "$SIF_CACHE_DIR" \
  --timeout "$TIMEOUT" \
  --max-attempts "$MAX_ATTEMPTS" \
  --retry-backoff "$RETRY_BACKOFF" \
  --failed-output "$SIF_CACHE_DIR/preheat_failed_images_\${SLURM_JOB_ID}.txt"
RC=\$?
echo "[sif-preheat] finished with rc=\$RC at \$(date)"
exit \$RC
EOF
)

ULHPC_CMD=(
  ulhpc-submit
  --submit-only
  --json
  --local-dir "$REPO_ROOT"
  --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME"
  --partition "$PARTITION"
  --cpus "$CPUS"
  --mem "$MEM"
  --time "$TIME"
  --gpus 0
  --module "$PYTHON_MODULE"
  --module "$CONTAINER_MODULE"
  --python python3
  --no-conda
  --stage-data "$DATASET_SNAPSHOT:$REMOTE_DATASET_SNAPSHOT"
  --link-as "$DATASET_REL"
  --apptainer-cache-dir "$REMOTE_APPTAINER_CACHE_DIR"
  --apptainer-tmp-dir "$REMOTE_APPTAINER_TMP_DIR"
  --apptainer-sif-cache-dir "$SIF_CACHE_DIR"
  --remote-ignore-extra
)

if [[ -n "$ULHPC_CONFIG" ]]; then
  ULHPC_CMD+=(--config "$ULHPC_CONFIG")
fi
if [[ "$FULL_LOGS" -eq 1 ]]; then
  ULHPC_CMD+=(--full-logs)
fi
if [[ "$SUBMIT" -eq 0 ]]; then
  ULHPC_CMD+=(--dry-run)
fi

ULHPC_CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[sif-preheat] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[sif-preheat] config=$CONFIG"
echo "[sif-preheat] remote-dir=$REMOTE_DIR"
echo "[sif-preheat] remote-dataset-snapshot=$REMOTE_DATASET_SNAPSHOT"
echo "[sif-preheat] sif-cache-dir=$SIF_CACHE_DIR"
echo "[sif-preheat] remote-apptainer-cache-dir=$REMOTE_APPTAINER_CACHE_DIR"
echo "[sif-preheat] remote-apptainer-tmp-dir=$REMOTE_APPTAINER_TMP_DIR"
echo "[sif-preheat] timeout=$TIMEOUT"
echo "[sif-preheat] max-attempts=$MAX_ATTEMPTS"
echo "[sif-preheat] install-deps=$INSTALL_DEPS"
echo "[sif-preheat] invoking ulhpc-submit..."

set +e
"${ULHPC_CMD[@]}"
ULHPC_RC=$?
set -e

if [[ $ULHPC_RC -ne 0 ]]; then
  echo "[sif-preheat] ulhpc-submit reported failure" >&2
fi

exit "$ULHPC_RC"
