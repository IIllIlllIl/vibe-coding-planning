#!/usr/bin/env bash
# Submit a GEPA rules optimization job to ULHPC via ulhpc-submit.
#
# This wrapper keeps the project-facing GEPA CLI stable while delegating the
# HPC mechanics to ulhpc-submit:
#   - dataset staging: ulhpc-submit --stage-data + --link-as
#   - resumable run_dir: ulhpc-submit --persistent-output
#   - Iris runtime: --module lang/Python/3.11 --module tools/Apptainer --no-conda
#   - Apptainer cache env: --apptainer-cache-dir / --apptainer-tmp-dir
#   - long-job behavior: --submit-only --json
#
# Default mode is dry-run; pass --submit to submit.
set -euo pipefail

SUBMIT=0
GEPA_RULES=0
GEPA_CONFIG=""
JOB_NAME="vibe-gepa"
PARTITION="batch"
TIME_LIMIT="02:00:00"
CPUS="2"
MEM="8G"
GPUS="0"
REMOTE_DIR=""
REMOTE_DATASET_DIR="~/hpc_datasets/vibe-coding-planning"
REMOTE_RUN_DIR="~/hpc_run_state/vibe-coding-planning"
REMOTE_APPTAINER_CACHE_DIR=""
REMOTE_APPTAINER_TMP_DIR=""
REMOTE_APPTAINER_SIF_CACHE_DIR=""
ULHPC_CONFIG=""
FULL_LOGS=0
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_submit_batch.sh --gepa-rules --gepa-config PATH [options]

Required:
  --gepa-rules              Flag to submit a GEPA rules optimization job
  --gepa-config PATH        Path to the GEPA config file

Slurm / ulhpc-submit options:
  --job-name NAME           Slurm job name (default: vibe-gepa)
  --partition NAME          Slurm partition (default: batch)
  --time HH:MM:SS           Wall time (default: 02:00:00)
  --cpus N                  CPUs per task (default: 2)
  --mem SIZE                Memory per node (default: 8G)
  --gpus N                  GPUs (default: 0)
  --remote-dir DIR          Remote project workdir on HPC
  --remote-dataset-dir DIR  Remote dataset staging root outside --remote-dir
                            (default: ~/hpc_datasets/vibe-coding-planning)
  --remote-run-dir DIR      Remote run state root outside --remote-dir
                            (default: ~/hpc_run_state/vibe-coding-planning)
  --remote-apptainer-cache-dir DIR
                            Remote APPTAINER_CACHEDIR
                            (default: ${VIBE_HPC_ROOT}/shared/apptainer-cache)
  --remote-apptainer-tmp-dir DIR
                            Remote APPTAINER_TMPDIR
                            (default: ${VIBE_HPC_ROOT}/shared/apptainer-tmp)
  --remote-apptainer-sif-cache-dir DIR
                            Remote shared SIF cache directory
                            (default: container.sif_cache_dir from GEPA config)
  --remote-env-file FILE    Remote shell env file sourced inside the Slurm job
                            (default: ~/.config/vibe-coding-planning/deepseek.env)
  --ulhpc-config FILE       ulhpc-submit config file
                            (default: configs/ulhpc_submit.yaml if present)
  --full-logs               Ask ulhpc-submit to retrieve full logs when not detached
  --submit                  Actually submit the job (default is dry-run)

Examples:
  bash scripts/hpc_submit_batch.sh \
    --gepa-rules \
    --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
    --remote-dir '~/hpc_runs/vibe-gepa-strict-newprompt' \
    --job-name gepa-strict-newprompt \
    --time 24:00:00 \
    --cpus 2 \
    --mem 8G \
    --submit
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gepa-rules)
      GEPA_RULES=1
      shift
      ;;
    --gepa-config)
      GEPA_CONFIG="$2"
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
    --time)
      TIME_LIMIT="$2"
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
    --gpus)
      GPUS="$2"
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
    --remote-apptainer-sif-cache-dir)
      REMOTE_APPTAINER_SIF_CACHE_DIR="$2"
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

if [[ $GEPA_RULES -ne 1 ]]; then
  echo "ERROR: --gepa-rules is required" >&2
  usage >&2
  exit 2
fi
if [[ -z "$GEPA_CONFIG" ]]; then
  echo "ERROR: --gepa-config is required" >&2
  usage >&2
  exit 2
fi
if ! [[ "$CPUS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --cpus must be a positive integer" >&2
  exit 2
fi
if ! [[ "$GPUS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --gpus must be a non-negative integer" >&2
  exit 2
fi
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEPA_CONFIG_ABS="$(python -c "from pathlib import Path; print(Path('$REPO_ROOT').resolve() / '$GEPA_CONFIG')")"
if [[ ! -f "$GEPA_CONFIG_ABS" ]]; then
  echo "ERROR: GEPA config not found: $GEPA_CONFIG" >&2
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
for value_name in REMOTE_ENV_FILE REMOTE_DATASET_DIR REMOTE_RUN_DIR \
  REMOTE_APPTAINER_CACHE_DIR REMOTE_APPTAINER_TMP_DIR; do
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

CONFIG_VALUES=$(python - "$GEPA_CONFIG_ABS" "${ULHPC_CONFIG:-}" <<'PY'
import sys
import os
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
    remote_user = os.environ.get("ULHPC_USER")
    if remote_user:
        raw = raw.replace("${USER}", remote_user).replace("$USER", remote_user)
    raw = os.path.expandvars(raw)
    value = Path(raw)
    return str(value if value.is_absolute() else repo_root / value)

print(f"dataset_snapshot={resolve(paths.get('dataset_snapshot', ''))}")
print(f"run_dir={resolve(paths.get('run_dir', ''))}")
print(f"initial_rules={resolve(paths.get('initial_rules', ''))}")
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
INITIAL_RULES=""
CONFIG_SIF_CACHE_DIR=""
CONFIG_PYTHON_MODULE=""
CONFIG_CONTAINER_MODULE=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    initial_rules) INITIAL_RULES="$VALUE" ;;
    sif_cache_dir) CONFIG_SIF_CACHE_DIR="$VALUE" ;;
    python_module) CONFIG_PYTHON_MODULE="$VALUE" ;;
    container_module) CONFIG_CONTAINER_MODULE="$VALUE" ;;
  esac
done <<< "$CONFIG_VALUES"

if [[ -z "$DATASET_SNAPSHOT" || ! -d "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot directory not found locally: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ -z "$RUN_DIR" ]]; then
  echo "ERROR: run_dir not found in GEPA config" >&2
  exit 2
fi
if [[ -z "$INITIAL_RULES" || ! -f "$INITIAL_RULES" ]]; then
  echo "ERROR: initial_rules file not found locally: $INITIAL_RULES" >&2
  exit 2
fi

PYTHON_MODULE="${ULHPC_PYTHON_MODULE:-${CONFIG_PYTHON_MODULE:-lang/Python/3.11}}"
CONTAINER_MODULE="${ULHPC_CONTAINER_MODULE:-${CONFIG_CONTAINER_MODULE:-tools/Apptainer}}"
if [[ -z "$REMOTE_APPTAINER_SIF_CACHE_DIR" ]]; then
  REMOTE_APPTAINER_SIF_CACHE_DIR="$CONFIG_SIF_CACHE_DIR"
fi
if [[ -z "$REMOTE_APPTAINER_SIF_CACHE_DIR" ]]; then
  echo "ERROR: --remote-apptainer-sif-cache-dir is required when GEPA config has no container.sif_cache_dir" >&2
  exit 2
fi
if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="~/hpc_runs/vibe-coding-planning"
fi

DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
RUN_DIR_REL="${RUN_DIR#$REPO_ROOT/}"
GEPA_CONFIG_REL="${GEPA_CONFIG_ABS#$REPO_ROOT/}"
if [[ "$DATASET_REL" == "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot must be inside the repository for --link-as: $DATASET_SNAPSHOT" >&2
  exit 2
fi
if [[ "$RUN_DIR_REL" == "$RUN_DIR" ]]; then
  echo "ERROR: run_dir must be inside the repository for --persistent-output: $RUN_DIR" >&2
  exit 2
fi
if [[ "$GEPA_CONFIG_REL" == "$GEPA_CONFIG_ABS" ]]; then
  echo "ERROR: GEPA config must be inside the repository: $GEPA_CONFIG_ABS" >&2
  exit 2
fi

REMOTE_DATASET_SNAPSHOT="$REMOTE_DATASET_DIR/$DATASET_REL"
REMOTE_RUN_SNAPSHOT="$REMOTE_RUN_DIR/$RUN_DIR_REL"

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[vibe-gepa] started at \$(date) on \$(hostname)"
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$REMOTE_APPTAINER_SIF_CACHE_DIR"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR" "\$ULHPC_APPTAINER_SIF_CACHE_DIR"
test -f "$DATASET_REL/manifest.json" || {
  echo "[vibe-gepa] dataset snapshot missing after staging: $DATASET_REL" >&2
  exit 2
}
REMOTE_ENV_FILE="$REMOTE_ENV_FILE"
if [[ "\$REMOTE_ENV_FILE" == "~/"* ]]; then
  REMOTE_ENV_FILE="\$HOME/\${REMOTE_ENV_FILE#\~/}"
fi
if [[ ! -f "\$REMOTE_ENV_FILE" ]]; then
  echo "[vibe-gepa] remote env file not found: \$REMOTE_ENV_FILE" >&2
  echo "[vibe-gepa] create it with chmod 600 and export DEEPSEEK_API_KEY inside" >&2
  exit 2
fi
set +x
source "\$REMOTE_ENV_FILE"
test -n "\${DEEPSEEK_API_KEY:-}" || {
  echo "[vibe-gepa] DEEPSEEK_API_KEY missing after sourcing \$REMOTE_ENV_FILE" >&2
  exit 2
}
python3 -m pip install --quiet --user -e third_party/gepa || true
python3 scripts/internal/run_gepa_rules.py --config "$GEPA_CONFIG_REL"
GEPA_RC=\$?
echo "[vibe-gepa] GEPA exited with rc=\$GEPA_RC at \$(date)"
exit \$GEPA_RC
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
  --time "$TIME_LIMIT"
  --gpus "$GPUS"
  --module "$PYTHON_MODULE"
  --module "$CONTAINER_MODULE"
  --python python3
  --no-conda
  --stage-data "$DATASET_SNAPSHOT:$REMOTE_DATASET_SNAPSHOT"
  --link-as "$DATASET_REL"
  --persistent-output "$RUN_DIR_REL:$REMOTE_RUN_SNAPSHOT"
  --apptainer-cache-dir "$REMOTE_APPTAINER_CACHE_DIR"
  --apptainer-tmp-dir "$REMOTE_APPTAINER_TMP_DIR"
  --apptainer-sif-cache-dir "$REMOTE_APPTAINER_SIF_CACHE_DIR"
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

echo "[hpc-submit] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[hpc-submit] gepa-config=$GEPA_CONFIG"
echo "[hpc-submit] remote-dir=$REMOTE_DIR"
echo "[hpc-submit] remote-dataset-snapshot=$REMOTE_DATASET_SNAPSHOT"
echo "[hpc-submit] remote-run-snapshot=$REMOTE_RUN_SNAPSHOT"
echo "[hpc-submit] remote-apptainer-cache-dir=$REMOTE_APPTAINER_CACHE_DIR"
echo "[hpc-submit] remote-apptainer-tmp-dir=$REMOTE_APPTAINER_TMP_DIR"
echo "[hpc-submit] remote-apptainer-sif-cache-dir=$REMOTE_APPTAINER_SIF_CACHE_DIR"
echo "[hpc-submit] remote-env-file=$REMOTE_ENV_FILE"
echo "[hpc-submit] dataset_snapshot=$DATASET_SNAPSHOT"
echo "[hpc-submit] run_dir=$RUN_DIR"
echo "[hpc-submit] invoking ulhpc-submit..."

set +e
"${ULHPC_CMD[@]}"
ULHPC_RC=$?
set -e

if [[ $ULHPC_RC -ne 0 ]]; then
  echo "[hpc-submit] ulhpc-submit reported failure" >&2
fi

exit "$ULHPC_RC"
