#!/usr/bin/env bash
# Submit a single-worker online GEPA resource pilot through ulhpc-submit.
#
# The wrapper intentionally delegates Slurm script generation and module setup
# to ulhpc-submit. The remote command runs one rollout worker directly; it does
# not submit a nested sbatch job.
set -euo pipefail

CONFIG="configs/gepa_online_planning_hpc_resource_pilot_20260706.yaml"
JOB_NAME="online-gepa-resource-pilot"
PARTITION="batch"
CPUS="1"
MEM="4G"
TIME="00:20:00"
LIMIT="1"
TASK_INDEX="0"
SPLIT="train"
REMOTE_DIR=""
REMOTE_DATASET_DIR=""
REMOTE_RUN_DIR=""
REMOTE_APPTAINER_CACHE_DIR=""
REMOTE_APPTAINER_TMP_DIR=""
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"
ULHPC_CONFIG=""
FULL_LOGS=0
SUBMIT=0
INSTALL_DEPS=0
INSTANCE_IDS=()

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/tools/submit_online_hpc_resource_pilot.sh [options]

Online pilot options:
  --config PATH          Online GEPA config file
                         (default: configs/gepa_online_planning_hpc_resource_pilot_20260706.yaml)
  --split train|validation
  --instance-id ID       Select one instance; may be repeated
  --limit N              Number of selected cases visible to the worker (default: 1)
  --task-index N         Which selected case to run (default: 0)

Slurm / ulhpc-submit options:
  --job-name NAME        Job name (default: online-gepa-resource-pilot)
  --partition NAME       Slurm partition (default: batch)
  --cpus N               CPUs per task (default: 1)
  --mem SIZE             Memory (default: 4G)
  --time HH:MM:SS        Wall time (default: 00:20:00)
  --remote-dir DIR       Remote project workdir
  --remote-dataset-dir DIR
  --remote-run-dir DIR
  --remote-apptainer-cache-dir DIR
  --remote-apptainer-tmp-dir DIR
  --remote-env-file FILE Remote private env file sourced inside the job
                         (default: ~/.config/vibe-coding-planning/deepseek.env)
  --ulhpc-config FILE    ulhpc-submit config file
                         (default: configs/ulhpc_submit.yaml if present)
  --install-deps         Run pip install --user -r requirements.txt before the worker
  --full-logs            Ask ulhpc-submit to retrieve full logs when monitored
  --submit               Actually submit the job (default is dry-run)

Examples:
  # 1-minute smoke to verify sync/module/bootstrap behavior.
  bash scripts/tools/submit_online_hpc_resource_pilot.sh --time 00:01:00 --submit

  # 20-minute resource measurement pilot.
  bash scripts/tools/submit_online_hpc_resource_pilot.sh --time 00:20:00 --submit
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --split)
      SPLIT="$2"
      shift 2
      ;;
    --instance-id)
      INSTANCE_IDS+=("$2")
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --task-index)
      TASK_INDEX="$2"
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
    --remote-dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    --remote-dataset-dir)
      REMOTE_DATASET_DIR="$2"
      shift 2
      ;;
    --remote-run-dir)
      REMOTE_RUN_DIR="$2"
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
    --remote-env-file)
      REMOTE_ENV_FILE="$2"
      shift 2
      ;;
    --ulhpc-config)
      ULHPC_CONFIG="$2"
      shift 2
      ;;
    --install-deps)
      INSTALL_DEPS=1
      shift
      ;;
    --full-logs)
      FULL_LOGS=1
      shift
      ;;
    --submit)
      SUBMIT=1
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

if [[ "$SPLIT" != "train" && "$SPLIT" != "validation" ]]; then
  echo "ERROR: --split must be train or validation" >&2
  exit 2
fi
for value_name in CPUS LIMIT TASK_INDEX; do
  if ! [[ "${!value_name}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: $value_name must be a non-negative integer" >&2
    exit 2
  fi
done
if [[ "$CPUS" -lt 1 || "$LIMIT" -lt 1 ]]; then
  echo "ERROR: --cpus and --limit must be positive" >&2
  exit 2
fi
if [[ "$TASK_INDEX" -ge "$LIMIT" ]]; then
  echo "ERROR: --task-index must be smaller than --limit" >&2
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
if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="${VIBE_HPC_ROOT}/online-ulhpc-resource-pilot"
fi
if [[ -z "$REMOTE_DATASET_DIR" ]]; then
  REMOTE_DATASET_DIR="${VIBE_HPC_ROOT}/datasets"
fi
if [[ -z "$REMOTE_RUN_DIR" ]]; then
  REMOTE_RUN_DIR="${VIBE_HPC_ROOT}/run_state"
fi
if [[ -z "$REMOTE_APPTAINER_CACHE_DIR" ]]; then
  REMOTE_APPTAINER_CACHE_DIR="${VIBE_HPC_ROOT}/shared/apptainer-cache"
fi
if [[ -z "$REMOTE_APPTAINER_TMP_DIR" ]]; then
  REMOTE_APPTAINER_TMP_DIR="${VIBE_HPC_ROOT}/shared/apptainer-tmp"
fi

if ! command -v ulhpc-submit >/dev/null 2>&1; then
  echo "ERROR: ulhpc-submit not found. Install the adjacent hpc_submit project:" >&2
  echo "  cd ../../hpc_submit && pip install -e \".[dev]\"" >&2
  exit 127
fi

CONFIG_VALUES=$(python - "$CONFIG_ABS" "${ULHPC_CONFIG:-}" <<'PY'
import sys
import os
from pathlib import Path

import yaml

config_path = Path(sys.argv[1])
ulhpc_config = Path(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else None
repo_root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
paths = cfg.get("paths", {})

def resolve(raw: str) -> str:
    if not raw:
        return ""
    remote_user = os.environ.get("ULHPC_USER")
    if remote_user:
        raw = raw.replace("${USER}", remote_user).replace("$USER", remote_user)
    raw = os.path.expandvars(raw)
    value = Path(raw)
    return str(value if value.is_absolute() else repo_root / value)

print(f"dataset_snapshot={resolve(paths.get('dataset_snapshot', ''))}")
print(f"run_dir={resolve(paths.get('run_dir', ''))}")
container = cfg.get("container", {}) or {}
raw_sif_cache = str(container.get("sif_cache_dir", ""))
remote_user = os.environ.get("ULHPC_USER")
if remote_user:
    raw_sif_cache = raw_sif_cache.replace("${USER}", remote_user).replace(
        "$USER", remote_user
    )
print(f"sif_cache_dir={os.path.expandvars(raw_sif_cache)}")

ulhpc = {}
if ulhpc_config and ulhpc_config.exists():
    ulhpc = yaml.safe_load(ulhpc_config.read_text(encoding="utf-8")) or {}
print(f"python_module={ulhpc.get('python_module', '')}")
print(f"container_module={ulhpc.get('container_module', '')}")
PY
)

DATASET_SNAPSHOT=""
RUN_DIR=""
SIF_CACHE_DIR=""
CONFIG_PYTHON_MODULE=""
CONFIG_CONTAINER_MODULE=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    sif_cache_dir) SIF_CACHE_DIR="$VALUE" ;;
    python_module) CONFIG_PYTHON_MODULE="$VALUE" ;;
    container_module) CONFIG_CONTAINER_MODULE="$VALUE" ;;
  esac
done <<< "$CONFIG_VALUES"

if [[ -z "$DATASET_SNAPSHOT" || ! -d "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot directory not found locally: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ -z "$RUN_DIR" ]]; then
  echo "ERROR: paths.run_dir is required in the online GEPA config" >&2
  exit 2
fi
if [[ -z "$SIF_CACHE_DIR" ]]; then
  echo "ERROR: container.sif_cache_dir is required in the online GEPA config" >&2
  exit 2
fi

PYTHON_MODULE="${ULHPC_PYTHON_MODULE:-${CONFIG_PYTHON_MODULE:-lang/Python/3.11}}"
CONTAINER_MODULE="${ULHPC_CONTAINER_MODULE:-${CONFIG_CONTAINER_MODULE:-tools/Apptainer}}"

DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
RUN_REL="${RUN_DIR#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
if [[ "$DATASET_REL" == "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot must be inside the repository for --link-as: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ "$RUN_REL" == "$RUN_DIR" ]]; then
  echo "ERROR: paths.run_dir must be inside the repository for --persistent-output: $RUN_DIR" >&2
  exit 2
fi
if [[ "$CONFIG_REL" == "$CONFIG_ABS" ]]; then
  echo "ERROR: config must be inside the repository: $CONFIG_ABS" >&2
  exit 2
fi

REMOTE_DATASET_SNAPSHOT="$REMOTE_DATASET_DIR/$DATASET_REL"
REMOTE_RUN_PATH="$REMOTE_RUN_DIR/$RUN_REL"

if [[ "$INSTALL_DEPS" -eq 1 ]]; then
  REMOTE_INSTALL_DEPS='python3 -m pip install --quiet --user -r requirements.txt'
else
  REMOTE_INSTALL_DEPS='echo "[online-resource-pilot] skipping dependency install; use --install-deps to enable"'
fi

INSTANCE_ARGS=""
for instance_id in "${INSTANCE_IDS[@]+"${INSTANCE_IDS[@]}"}"; do
  INSTANCE_ARGS+=" --instance-id $(printf '%q' "$instance_id")"
done

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
set +x
echo "[online-resource-pilot] started at \$(date) on \$(hostname)"
ENV_FILE="$REMOTE_ENV_FILE"
ENV_FILE="\${ENV_FILE/#\\~/\$HOME}"
source "\$ENV_FILE"
test -n "\${DEEPSEEK_API_KEY:-}" || {
  echo "[online-resource-pilot] missing DEEPSEEK_API_KEY" >&2
  exit 2
}
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$SIF_CACHE_DIR"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR" "\$ULHPC_APPTAINER_SIF_CACHE_DIR"
test -f "$DATASET_REL/manifest.json" || {
  echo "[online-resource-pilot] dataset snapshot missing after staging: $DATASET_REL" >&2
  exit 2
}
$REMOTE_INSTALL_DEPS
python3 scripts/tools/run_online_hpc_resource_worker.py \
  --config "$CONFIG_REL" \
  --split "$SPLIT" \
  --limit "$LIMIT" \
  --task-index "$TASK_INDEX"$INSTANCE_ARGS
RC=\$?
echo "[online-resource-pilot] finished with rc=\$RC at \$(date)"
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
  --persistent-output "$RUN_REL:$REMOTE_RUN_PATH"
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

echo "[online-resource-pilot] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[online-resource-pilot] config=$CONFIG"
echo "[online-resource-pilot] remote-dir=$REMOTE_DIR"
echo "[online-resource-pilot] remote-dataset-snapshot=$REMOTE_DATASET_SNAPSHOT"
echo "[online-resource-pilot] remote-run-path=$REMOTE_RUN_PATH"
echo "[online-resource-pilot] sif-cache-dir=$SIF_CACHE_DIR"
echo "[online-resource-pilot] remote-apptainer-cache-dir=$REMOTE_APPTAINER_CACHE_DIR"
echo "[online-resource-pilot] remote-apptainer-tmp-dir=$REMOTE_APPTAINER_TMP_DIR"
echo "[online-resource-pilot] cpus=$CPUS mem=$MEM time=$TIME"
echo "[online-resource-pilot] install-deps=$INSTALL_DEPS"
echo "[online-resource-pilot] invoking ulhpc-submit..."

set +e
"${ULHPC_CMD[@]}"
ULHPC_RC=$?
set -e

if [[ $ULHPC_RC -ne 0 ]]; then
  echo "[online-resource-pilot] ulhpc-submit reported failure" >&2
fi

exit "$ULHPC_RC"
