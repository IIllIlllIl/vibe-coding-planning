#!/usr/bin/env bash
# Advance one independent SWE-Verified PCCE controller slice through ulhpc-submit.
set -euo pipefail

SUBMIT=0
CONFIG=""
JOB_NAME="swe-verified-pcce-controller"
PARTITION="batch"
TIME_LIMIT="00:10:00"
CPUS="1"
MEM="4G"
REMOTE_DIR="~/hpc_runs/vibe-swe-verified-pcce"
REMOTE_DATASET_DIR="~/hpc_datasets/vibe-coding-planning"
REMOTE_RUN_DIR="~/hpc_run_state/vibe-coding-planning"
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"
ULHPC_CONFIG=""
REQUIRE_CLEAN=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_submit_swe_verified_pcce.sh --config PATH [options]

Required:
  --config PATH             mode: swe_verified_pcce runtime config

Options:
  --job-name NAME           controller job name
  --time HH:MM:SS           controller slice walltime (default: 00:10:00)
  --remote-dir DIR          remote synced project directory
  --require-clean-worktree  reject an uncommitted source/config identity
  --submit                  submit; default is ulhpc-submit dry-run
  --dry-run                 explicitly retain dry-run mode

Re-run the same command and config to collect an existing PC/CE batch or
submit the next fingerprinted workflow phase. Worker resources come from the
PCCE config; each array element is one isolated PC or CE task.
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
    --ulhpc-config) ULHPC_CONFIG="$2"; shift 2 ;;
    --require-clean-worktree) REQUIRE_CLEAN=1; shift ;;
    --submit) SUBMIT=1; shift ;;
    --dry-run) SUBMIT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(conda run --no-capture-output -n mini-swe python - "${BASH_SOURCE[0]}" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).resolve().parents[1])
PY
)"
if [[ -z "$CONFIG" ]]; then
  echo "ERROR: --config is required" >&2
  exit 2
fi
CONFIG_ABS="$(conda run --no-capture-output -n mini-swe python - "$REPO_ROOT" "$CONFIG" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
path = Path(sys.argv[2])
print((path if path.is_absolute() else root / path).resolve())
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
if data.get("mode") != "swe_verified_pcce":
    raise SystemExit("config mode must be swe_verified_pcce")
root = path.parents[1] if path.parent.name == "configs" else Path.cwd()
def resolve(value):
    candidate = Path(os.path.expandvars(str(value))).expanduser()
    # Preserve the configured worktree-relative identity across shared-output
    # symlinks; ulhpc-submit stages the bytes under this logical path.
    return candidate.absolute() if candidate.is_absolute() else (root / candidate).absolute()
paths = data.get("paths") or {}
pce_path = resolve(paths["pce_runtime_config"])
pce = yaml.safe_load(pce_path.read_text(encoding="utf-8")) or {}
container = pce.get("container") or {}
for key in ("source_snapshot", "pce_outcomes", "run_dir"):
    print(key + "=" + str(resolve(paths[key])))
print("image_manifest=" + str(resolve(paths["image_manifest"])))
print("sif_cache_dir=" + str(container["sif_cache_dir"]))
PY
)"

SOURCE_SNAPSHOT=""
PCE_OUTCOMES=""
IMAGE_MANIFEST=""
RUN_DIR=""
SIF_CACHE_DIR=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    source_snapshot) SOURCE_SNAPSHOT="$VALUE" ;;
    pce_outcomes) PCE_OUTCOMES="$VALUE" ;;
    image_manifest) IMAGE_MANIFEST="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    sif_cache_dir) SIF_CACHE_DIR="$VALUE" ;;
  esac
done <<< "$VALUES"
PCE_BASELINE_DIR="$(dirname "$PCE_OUTCOMES")"
PCE_STAGE_DIR=""
PCE_BASELINE_STAGE="single-file frozen outcome bundle"

for required in "$SOURCE_SNAPSHOT/manifest.json" "$PCE_OUTCOMES" "$IMAGE_MANIFEST"; do
  if [[ ! -f "$required" ]]; then
    echo "ERROR: required frozen input missing: $required" >&2
    exit 2
  fi
done
case "$IMAGE_MANIFEST" in "$SOURCE_SNAPSHOT"/*) ;; *)
  echo "ERROR: image_manifest must be inside source_snapshot for atomic staging" >&2
  exit 2;; esac
for local_path in "$SOURCE_SNAPSHOT" "$PCE_BASELINE_DIR" "$RUN_DIR" "$CONFIG_ABS"; do
  case "$local_path" in "$REPO_ROOT"/*) ;; *)
    echo "ERROR: PCCE paths must be inside the repository: $local_path" >&2
    exit 2;; esac
done
PCE_STAGE_DIR="$(mktemp -d /tmp/swe-verified-pcce-pce.XXXXXX)"
trap 'rm -rf "$PCE_STAGE_DIR"' EXIT
cp "$PCE_OUTCOMES" "$PCE_STAGE_DIR/$(basename "$PCE_OUTCOMES")"

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
SIF_CACHE_DIR="${SIF_CACHE_DIR//\$\{USER\}/$REMOTE_USER}"

SOURCE_REL="${SOURCE_SNAPSHOT#$REPO_ROOT/}"
PCE_BASELINE_REL="${PCE_BASELINE_DIR#$REPO_ROOT/}"
RUN_REL="${RUN_DIR#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
REMOTE_SOURCE="$REMOTE_DATASET_DIR/$SOURCE_REL"
REMOTE_PCE_BASELINE="$REMOTE_DATASET_DIR/$PCE_BASELINE_REL"
REMOTE_RUN="$REMOTE_RUN_DIR/$RUN_REL"

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
export APPTAINER_CACHEDIR="$HPC_ROOT/shared/apptainer-cache"
export APPTAINER_TMPDIR="$HPC_ROOT/shared/apptainer-tmp"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$SIF_CACHE_DIR"
export VIBE_CONTROLLER_GIT_HEAD="$LOCAL_GIT_HEAD"
RUN_MANIFEST="$RUN_REL/run_manifest.json"
if [[ -f "\$RUN_MANIFEST" ]]; then
  VIBE_PROJECT_GIT_HEAD="\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["project_git_head"])' "\$RUN_MANIFEST")"
else
  VIBE_PROJECT_GIT_HEAD="$LOCAL_GIT_HEAD"
fi
export VIBE_PROJECT_GIT_HEAD
echo "[swe-verified-pcce-controller] source_git_head=\$VIBE_CONTROLLER_GIT_HEAD run_git_head=\$VIBE_PROJECT_GIT_HEAD"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR"
REMOTE_ENV_FILE="$REMOTE_ENV_FILE"
if [[ "\$REMOTE_ENV_FILE" == "~/"* ]]; then
  REMOTE_ENV_FILE="\$HOME/\${REMOTE_ENV_FILE#\~/}"
fi
set +x
source "\$REMOTE_ENV_FILE"
test -n "\${DEEPSEEK_API_KEY:-}" || exit 2
python3 scripts/run_swe_verified_pcce_hpc.py --config "$CONFIG_REL"
EOF
)

CMD=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$REPO_ROOT" --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME" --partition "$PARTITION"
  --cpus "$CPUS" --mem "$MEM" --time "$TIME_LIMIT" --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --stage-data "$SOURCE_SNAPSHOT:$REMOTE_SOURCE" --link-as "$SOURCE_REL"
)
if [[ -n "$PCE_STAGE_DIR" ]]; then
  CMD+=(
    --stage-data "$PCE_STAGE_DIR:$REMOTE_PCE_BASELINE" --link-as "$PCE_BASELINE_REL"
  )
fi
CMD+=(
  --persistent-output "$RUN_REL:$REMOTE_RUN"
  --apptainer-cache-dir "$HPC_ROOT/shared/apptainer-cache"
  --apptainer-tmp-dir "$HPC_ROOT/shared/apptainer-tmp"
  --apptainer-sif-cache-dir "$SIF_CACHE_DIR"
  --remote-ignore-extra --config "$ULHPC_CONFIG"
)
[[ $SUBMIT -eq 0 ]] && CMD+=(--dry-run)
CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[swe-verified-pcce-submit] mode=$([[ $SUBMIT -eq 1 ]] && echo submit || echo dry-run)"
echo "[swe-verified-pcce-submit] config=$CONFIG_REL"
echo "[swe-verified-pcce-submit] source=$SOURCE_REL"
echo "[swe-verified-pcce-submit] baseline=$PCE_BASELINE_REL"
echo "[swe-verified-pcce-submit] baseline_stage=$PCE_BASELINE_STAGE"
echo "[swe-verified-pcce-submit] run=$RUN_REL"
echo "[swe-verified-pcce-submit] controller_resources=$CPUS CPU/$MEM/$TIME_LIMIT"
"${CMD[@]}"
