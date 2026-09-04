#!/usr/bin/env bash
# Advance one fixed-guideline Checker-only controller slice through ulhpc-submit.
# Default mode is dry-run. The controller submits one Slurm worker per assignment.
set -euo pipefail

SUBMIT=0
CONFIG=""
JOB_NAME="swe-verified-pc-only-controller"
PARTITION="batch"
TIME_LIMIT="00:10:00"
CPUS="1"
MEM="4G"
REMOTE_DIR="~/hpc_runs/vibe-swe-verified-pc-only"
REMOTE_RUN_DIR="~/hpc_run_state/vibe-coding-planning"
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"
ULHPC_CONFIG=""
REQUIRE_CLEAN=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_submit_offline_check_only.sh --config PATH [options]

Required:
  --config PATH             mode: offline_check_only runtime config

Options:
  --job-name NAME           controller job name
  --time HH:MM:SS           controller slice walltime (default: 00:10:00)
  --remote-dir DIR          remote synced project directory
  --remote-run-dir DIR      remote persistent run-state root
  --remote-env-file FILE    remote DeepSeek environment file
  --ulhpc-config FILE       worktree-local ULHPC config reference
  --require-clean-worktree  reject an uncommitted source/config identity
  --submit                  submit; default is ulhpc-submit dry-run
  --dry-run                 explicitly retain dry-run mode

Worker resources, walltime, and retry policy come from the referenced Checker
runtime config. Re-run the same command to collect or retry its fingerprinted
task batch.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    --partition) PARTITION="$2"; shift 2 ;;
    --time) TIME_LIMIT="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --mem) MEM="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --remote-run-dir) REMOTE_RUN_DIR="$2"; shift 2 ;;
    --remote-env-file) REMOTE_ENV_FILE="$2"; shift 2 ;;
    --ulhpc-config) ULHPC_CONFIG="$2"; shift 2 ;;
    --require-clean-worktree) REQUIRE_CLEAN=1; shift ;;
    --submit) SUBMIT=1; shift ;;
    --dry-run) SUBMIT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(
  conda run --no-capture-output -n mini-swe python - "${BASH_SOURCE[0]}" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).resolve().parents[1])
PY
)"
if [[ -z "$CONFIG" ]]; then
  echo "ERROR: --config is required" >&2
  exit 2
fi
CONFIG_ABS="$(
  conda run --no-capture-output -n mini-swe python - "$REPO_ROOT" "$CONFIG" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
config = Path(sys.argv[2])
print((config if config.is_absolute() else root / config).absolute())
PY
)"
if [[ ! -f "$CONFIG_ABS" ]]; then
  echo "ERROR: config not found: $CONFIG" >&2
  exit 2
fi
if [[ $REQUIRE_CLEAN -eq 1 ]] && [[ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]]; then
  echo "ERROR: --require-clean-worktree requires a clean Git worktree" >&2
  exit 2
fi
LOCAL_GIT_HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [[ -z "$ULHPC_CONFIG" ]]; then
  ULHPC_CONFIG="$REPO_ROOT/configs/ulhpc_submit.yaml"
fi

VALUES="$(
  cd "$REPO_ROOT"
  conda run --no-capture-output -n mini-swe python - "$CONFIG_ABS" <<'PY'
import sys
from pathlib import Path

from src.offline_check_only.config import load_check_only_config

config = load_check_only_config(Path(sys.argv[1]), require_api_keys=False)
print("dataset_snapshot=" + str(config.dataset.snapshot.absolute()))
print("guideline_bundle=" + str(config.guideline_bundle.absolute()))
print("run_dir=" + str(config.run_dir.absolute()))
print("sif_cache_dir=" + str(config.runtime.container.sif_cache_dir))
PY
)"

DATASET_SNAPSHOT=""
GUIDELINE_BUNDLE=""
RUN_DIR=""
SIF_CACHE_DIR=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    guideline_bundle) GUIDELINE_BUNDLE="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    sif_cache_dir) SIF_CACHE_DIR="$VALUE" ;;
  esac
done <<< "$VALUES"

for required in \
  "$DATASET_SNAPSHOT/manifest.json" \
  "$DATASET_SNAPSHOT/cases.jsonl" \
  "$GUIDELINE_BUNDLE/manifest.json"; do
  if [[ ! -f "$required" ]]; then
    echo "ERROR: required frozen input missing: $required" >&2
    exit 2
  fi
done
for path in "$CONFIG_ABS" "$DATASET_SNAPSHOT" "$GUIDELINE_BUNDLE" "$RUN_DIR"; do
  case "$path" in "$REPO_ROOT"/*) ;; *)
    echo "ERROR: project path must be inside the worktree: $path" >&2
    exit 2
  esac
done

ULHPC_SUBMIT_BIN="${ULHPC_SUBMIT_BIN:-}"
if [[ -z "$ULHPC_SUBMIT_BIN" ]] && command -v ulhpc-submit >/dev/null 2>&1; then
  ULHPC_SUBMIT_BIN="$(command -v ulhpc-submit)"
fi
if [[ -z "$ULHPC_SUBMIT_BIN" && -n "${CONDA_EXE:-}" ]]; then
  CANDIDATE="$(dirname "$CONDA_EXE")/ulhpc-submit"
  [[ -x "$CANDIDATE" ]] && ULHPC_SUBMIT_BIN="$CANDIDATE"
fi
if [[ -z "$ULHPC_SUBMIT_BIN" || ! -x "$ULHPC_SUBMIT_BIN" ]]; then
  echo "ERROR: ulhpc-submit not found" >&2
  exit 127
fi

REMOTE_USER="$(
  conda run --no-capture-output -n mini-swe python - "$ULHPC_CONFIG" <<'PY'
import os
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text()) if path.is_file() else {}
print(os.environ.get("ULHPC_USER") or (data or {}).get("user") or "")
PY
)"
if [[ -z "$REMOTE_USER" ]]; then
  echo "ERROR: ULHPC user is unavailable" >&2
  exit 2
fi
export ULHPC_USER="${ULHPC_USER:-$REMOTE_USER}"
HPC_ROOT="/scratch/users/${REMOTE_USER}/vibe-coding-planning"
SIF_CACHE_DIR="${SIF_CACHE_DIR//\$\{USER\}/$REMOTE_USER}"
REMOTE_APPTAINER_CACHE_DIR="$HPC_ROOT/shared/apptainer-cache"
REMOTE_APPTAINER_TMP_DIR="$HPC_ROOT/shared/apptainer-tmp"

RUN_REL="${RUN_DIR#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
REMOTE_RUN="$REMOTE_RUN_DIR/$RUN_REL"

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$SIF_CACHE_DIR"
export VIBE_PROJECT_GIT_HEAD="$LOCAL_GIT_HEAD"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR"
REMOTE_ENV_FILE="$REMOTE_ENV_FILE"
if [[ "\$REMOTE_ENV_FILE" == "~/"* ]]; then
  REMOTE_ENV_FILE="\$HOME/\${REMOTE_ENV_FILE#\~/}"
fi
set +x
source "\$REMOTE_ENV_FILE"
test -n "\${DEEPSEEK_API_KEY:-}" || exit 2
python3 scripts/run_offline_check_only.py --config "$CONFIG_REL"
EOF
)

CMD=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$REPO_ROOT" --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME" --partition "$PARTITION"
  --cpus "$CPUS" --mem "$MEM" --time "$TIME_LIMIT" --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --persistent-output "$RUN_REL:$REMOTE_RUN"
  --apptainer-cache-dir "$REMOTE_APPTAINER_CACHE_DIR"
  --apptainer-tmp-dir "$REMOTE_APPTAINER_TMP_DIR"
  --apptainer-sif-cache-dir "$SIF_CACHE_DIR"
  --remote-ignore-extra --config "$ULHPC_CONFIG"
)
[[ $SUBMIT -eq 0 ]] && CMD+=(--dry-run)
CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[offline-check-only-submit] mode=$([[ $SUBMIT -eq 1 ]] && echo submit || echo dry-run)"
echo "[offline-check-only-submit] config=$CONFIG_REL"
echo "[offline-check-only-submit] dataset=${DATASET_SNAPSHOT#$REPO_ROOT/}"
echo "[offline-check-only-submit] run=$RUN_REL"
echo "[offline-check-only-submit] controller_resources=$CPUS CPU/$MEM/$TIME_LIMIT"
"${CMD[@]}"
