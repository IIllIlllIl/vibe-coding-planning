#!/usr/bin/env bash
# Advance one independent SWE-Verified PCE controller slice through ulhpc-submit.
# Default mode is dry-run. This entry point does not call GEPA or Online code.
set -euo pipefail

SUBMIT=0
CONFIG=""
JOB_NAME="swe-verified-pce-controller"
PARTITION="batch"
TIME_LIMIT="00:10:00"
CPUS="1"
MEM="4G"
REMOTE_DIR="~/hpc_runs/vibe-swe-verified-pce"
REMOTE_DATASET_DIR="~/hpc_datasets/vibe-coding-planning"
REMOTE_RUN_DIR="~/hpc_run_state/vibe-coding-planning"
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"
REMOTE_APPTAINER_CACHE_DIR=""
REMOTE_APPTAINER_TMP_DIR=""
REMOTE_APPTAINER_SIF_CACHE_DIR=""
ULHPC_CONFIG=""
REQUIRE_CLEAN=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_submit_swe_verified_pce.sh --config PATH [options]

Required:
  --config PATH             mode: swe_verified_pce runtime config

Options:
  --job-name NAME           controller job name
  --time HH:MM:SS           controller slice walltime (default: 00:10:00)
  --remote-dir DIR          remote synced project directory
  --require-clean-worktree  reject an uncommitted source/config identity
  --submit                  submit; default is ulhpc-submit dry-run
  --dry-run                 explicitly retain dry-run mode

The worker resources and hard walltime come from the PCE config. Re-run
this same command with the same config to collect or selectively retry the
fingerprinted task batch after the first controller yields.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    --partition) PARTITION="$2"; shift 2 ;;
    --time) TIME_LIMIT="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --remote-dataset-dir) REMOTE_DATASET_DIR="$2"; shift 2 ;;
    --remote-run-dir) REMOTE_RUN_DIR="$2"; shift 2 ;;
    --remote-env-file) REMOTE_ENV_FILE="$2"; shift 2 ;;
    --remote-apptainer-cache-dir) REMOTE_APPTAINER_CACHE_DIR="$2"; shift 2 ;;
    --remote-apptainer-tmp-dir) REMOTE_APPTAINER_TMP_DIR="$2"; shift 2 ;;
    --remote-apptainer-sif-cache-dir) REMOTE_APPTAINER_SIF_CACHE_DIR="$2"; shift 2 ;;
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
print((config if config.is_absolute() else root / config).resolve())
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

VALUES="$(conda run --no-capture-output -n mini-swe python - "$CONFIG_ABS" <<'PY'
import os
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
if data.get("mode") != "swe_verified_pce":
    raise SystemExit("config mode must be swe_verified_pce")
root = path.parents[1] if path.parent.name == "configs" else Path.cwd()
def resolve(value):
    candidate = Path(os.path.expandvars(str(value))).expanduser()
    # Keep the worktree-relative logical path. The local output tree may be a
    # deliberate symlink to shared evidence; resolving it would make the
    # staged link identity escape this worktree.
    return candidate.absolute() if candidate.is_absolute() else (root / candidate).absolute()
paths = data.get("paths") or {}
container = data.get("container") or {}
print("dataset_snapshot=" + str(resolve(paths["dataset_snapshot"])))
print("image_manifest=" + str(resolve(paths["image_manifest"])))
print("run_dir=" + str(resolve(paths["run_dir"])))
print("sif_cache_dir=" + str(container["sif_cache_dir"]))
PY
)"

DATASET_SNAPSHOT=""
IMAGE_MANIFEST=""
RUN_DIR=""
CONFIG_SIF_CACHE_DIR=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    image_manifest) IMAGE_MANIFEST="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    sif_cache_dir) CONFIG_SIF_CACHE_DIR="$VALUE" ;;
  esac
done <<< "$VALUES"

for required in "$DATASET_SNAPSHOT/manifest.json" "$IMAGE_MANIFEST"; do
  if [[ ! -f "$required" ]]; then
    echo "ERROR: required frozen input missing: $required" >&2
    exit 2
  fi
done
case "$DATASET_SNAPSHOT" in "$REPO_ROOT"/*) ;; *)
  echo "ERROR: dataset_snapshot must be inside the repository" >&2; exit 2;; esac
case "$IMAGE_MANIFEST" in "$DATASET_SNAPSHOT"/*) ;; *)
  echo "ERROR: image_manifest must be inside dataset_snapshot for atomic staging" >&2; exit 2;; esac
case "$RUN_DIR" in "$REPO_ROOT"/*) ;; *)
  echo "ERROR: run_dir must be inside the repository" >&2; exit 2;; esac
case "$CONFIG_ABS" in "$REPO_ROOT"/*) ;; *)
  echo "ERROR: config must be inside the repository" >&2; exit 2;; esac

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

REMOTE_USER="$(conda run --no-capture-output -n mini-swe python - "$ULHPC_CONFIG" <<'PY'
import os, sys, yaml
from pathlib import Path
p = Path(sys.argv[1])
d = yaml.safe_load(p.read_text()) if p.is_file() else {}
print(os.environ.get("ULHPC_USER") or (d or {}).get("user") or "")
PY
)"
if [[ -z "$REMOTE_USER" ]]; then
  echo "ERROR: ULHPC user is unavailable" >&2
  exit 2
fi
export ULHPC_USER="${ULHPC_USER:-$REMOTE_USER}"
HPC_ROOT="/scratch/users/${REMOTE_USER}/vibe-coding-planning"
CONFIG_SIF_CACHE_DIR="${CONFIG_SIF_CACHE_DIR//\$\{USER\}/$REMOTE_USER}"
REMOTE_APPTAINER_CACHE_DIR="${REMOTE_APPTAINER_CACHE_DIR:-$HPC_ROOT/shared/apptainer-cache}"
REMOTE_APPTAINER_TMP_DIR="${REMOTE_APPTAINER_TMP_DIR:-$HPC_ROOT/shared/apptainer-tmp}"
REMOTE_APPTAINER_SIF_CACHE_DIR="${REMOTE_APPTAINER_SIF_CACHE_DIR:-$CONFIG_SIF_CACHE_DIR}"

DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
RUN_REL="${RUN_DIR#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
REMOTE_DATASET="$REMOTE_DATASET_DIR/$DATASET_REL"
REMOTE_RUN="$REMOTE_RUN_DIR/$RUN_REL"

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$REMOTE_APPTAINER_SIF_CACHE_DIR"
export VIBE_PROJECT_GIT_HEAD="$LOCAL_GIT_HEAD"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR"
REMOTE_ENV_FILE="$REMOTE_ENV_FILE"
if [[ "\$REMOTE_ENV_FILE" == "~/"* ]]; then
  REMOTE_ENV_FILE="\$HOME/\${REMOTE_ENV_FILE#\~/}"
fi
set +x
source "\$REMOTE_ENV_FILE"
test -n "\${DEEPSEEK_API_KEY:-}" || exit 2
python3 scripts/run_swe_verified_pce_hpc.py --config "$CONFIG_REL"
EOF
)

CMD=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$REPO_ROOT" --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME" --partition "$PARTITION"
  --cpus "$CPUS" --mem "$MEM" --time "$TIME_LIMIT" --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --stage-data "$DATASET_SNAPSHOT:$REMOTE_DATASET" --link-as "$DATASET_REL"
  --persistent-output "$RUN_REL:$REMOTE_RUN"
  --apptainer-cache-dir "$REMOTE_APPTAINER_CACHE_DIR"
  --apptainer-tmp-dir "$REMOTE_APPTAINER_TMP_DIR"
  --apptainer-sif-cache-dir "$REMOTE_APPTAINER_SIF_CACHE_DIR"
  --remote-ignore-extra --config "$ULHPC_CONFIG"
)
[[ $SUBMIT -eq 0 ]] && CMD+=(--dry-run)
CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[swe-verified-pce-submit] mode=$([[ $SUBMIT -eq 1 ]] && echo submit || echo dry-run)"
echo "[swe-verified-pce-submit] config=$CONFIG_REL"
echo "[swe-verified-pce-submit] dataset=$DATASET_REL"
echo "[swe-verified-pce-submit] run=$RUN_REL"
echo "[swe-verified-pce-submit] controller_resources=$CPUS CPU/$MEM/$TIME_LIMIT"
"${CMD[@]}"
