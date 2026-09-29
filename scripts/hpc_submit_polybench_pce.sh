#!/usr/bin/env bash
# Advance one independent PolyBench PCE controller slice through ulhpc-submit.
# Default mode is dry-run. This entry point does not call GEPA or Online code.
set -euo pipefail

SUBMIT=0
CONFIG=""
JOB_NAME="polybench-pce-controller"
PARTITION="batch"
TIME_LIMIT="00:10:00"
CPUS="1"
MEM="4G"
REMOTE_DIR="~/hpc_runs/vibe-polybench-pce"
REMOTE_DATASET_DIR="~/hpc_datasets/vibe-coding-planning"
REMOTE_RUN_DIR="~/hpc_run_state/vibe-coding-planning"
REMOTE_ENV_FILE="~/.config/vibe-coding-planning/deepseek.env"
REMOTE_APPTAINER_CACHE_DIR=""
REMOTE_APPTAINER_TMP_DIR=""
REMOTE_APPTAINER_SIF_CACHE_DIR=""
ULHPC_CONFIG=""
REQUIRE_CLEAN=0
EVALUATOR_REPAIR_ID=""
EVALUATOR_REPAIR_INSTANCES=()
EVALUATOR_REPAIR_INSTANCE_COUNT=0
EVALUATOR_REPAIR_INSTANCES_FILE=""
PREHEAT_HISTORY=0
FIXED_WORKTREE=0

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_submit_polybench_pce.sh --config PATH [options]

Required:
  --config PATH             mode: polybench_pce runtime config

Options:
  --job-name NAME           controller job name
  --time HH:MM:SS           controller slice walltime (default: 00:10:00)
  --mem SIZE                controller memory: 4G on Iris or 1750M on Aion
  --remote-dir DIR          remote synced project directory
  --require-clean-worktree  reject an uncommitted source/config identity
  --resume-evaluator ID     reuse validated Plan/Code checkpoints and rerun only Evaluate
  --resume-evaluator-instance ID
                            restrict repair to this instance; repeat as needed
  --resume-evaluator-instances-file PATH
                            frozen JSON subset shared by PCE and PCCE
  --preheat-history         prepare selected base-ancestor Git bundles only;
                            no Agent, PCE, or private API environment
  --fixed-worktree          reuse an exact scratch staging tree via rsync;
                            requires --remote-dir under project staging
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
    --mem) MEM="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --remote-dataset-dir) REMOTE_DATASET_DIR="$2"; shift 2 ;;
    --remote-run-dir) REMOTE_RUN_DIR="$2"; shift 2 ;;
    --remote-env-file) REMOTE_ENV_FILE="$2"; shift 2 ;;
    --remote-apptainer-cache-dir) REMOTE_APPTAINER_CACHE_DIR="$2"; shift 2 ;;
    --remote-apptainer-tmp-dir) REMOTE_APPTAINER_TMP_DIR="$2"; shift 2 ;;
    --remote-apptainer-sif-cache-dir) REMOTE_APPTAINER_SIF_CACHE_DIR="$2"; shift 2 ;;
    --ulhpc-config) ULHPC_CONFIG="$2"; shift 2 ;;
    --require-clean-worktree) REQUIRE_CLEAN=1; shift ;;
    --resume-evaluator) EVALUATOR_REPAIR_ID="$2"; shift 2 ;;
    --resume-evaluator-instance)
      EVALUATOR_REPAIR_INSTANCES+=("$2")
      EVALUATOR_REPAIR_INSTANCE_COUNT=$((EVALUATOR_REPAIR_INSTANCE_COUNT + 1))
      shift 2
      ;;
    --resume-evaluator-instances-file)
      EVALUATOR_REPAIR_INSTANCES_FILE="$2"
      shift 2
      ;;
    --preheat-history) PREHEAT_HISTORY=1; shift ;;
    --fixed-worktree) FIXED_WORKTREE=1; shift ;;
    --submit) SUBMIT=1; shift ;;
    --dry-run) SUBMIT=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$MEM" != "4G" && "$MEM" != "1750M" ]]; then
  echo "ERROR: --mem must be 4G or 1750M" >&2
  exit 2
fi
if [[ $PREHEAT_HISTORY -eq 1 ]] && [[ -n "$EVALUATOR_REPAIR_ID" || $EVALUATOR_REPAIR_INSTANCE_COUNT -gt 0 || -n "$EVALUATOR_REPAIR_INSTANCES_FILE" ]]; then
  echo "ERROR: --preheat-history cannot be combined with evaluator repair" >&2
  exit 2
fi

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
if [[ -n "$EVALUATOR_REPAIR_ID" ]] && [[ ! "$EVALUATOR_REPAIR_ID" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "ERROR: --resume-evaluator must match [A-Za-z0-9_.-]+" >&2
  exit 2
fi
if [[ $EVALUATOR_REPAIR_INSTANCE_COUNT -gt 0 ]] && [[ -z "$EVALUATOR_REPAIR_ID" ]]; then
  echo "ERROR: --resume-evaluator-instance requires --resume-evaluator" >&2
  exit 2
fi
if [[ -n "$EVALUATOR_REPAIR_INSTANCES_FILE" ]] && [[ -z "$EVALUATOR_REPAIR_ID" ]]; then
  echo "ERROR: --resume-evaluator-instances-file requires --resume-evaluator" >&2
  exit 2
fi
if [[ $EVALUATOR_REPAIR_INSTANCE_COUNT -gt 0 ]] && [[ -n "$EVALUATOR_REPAIR_INSTANCES_FILE" ]]; then
  echo "ERROR: evaluator instance flags and file are mutually exclusive" >&2
  exit 2
fi
if [[ $EVALUATOR_REPAIR_INSTANCE_COUNT -gt 0 ]]; then
  for instance_id in "${EVALUATOR_REPAIR_INSTANCES[@]}"; do
    if [[ ! "$instance_id" =~ ^[A-Za-z0-9_.-]+$ ]]; then
      echo "ERROR: --resume-evaluator-instance must match [A-Za-z0-9_.-]+" >&2
      exit 2
    fi
  done
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
EVALUATOR_REPAIR_INSTANCES_FILE_ABS=""
EVALUATOR_REPAIR_INSTANCES_FILE_REL=""
if [[ -n "$EVALUATOR_REPAIR_INSTANCES_FILE" ]]; then
  EVALUATOR_REPAIR_INSTANCES_FILE_ABS="$(
    conda run --no-capture-output -n mini-swe python - "$REPO_ROOT" "$EVALUATOR_REPAIR_INSTANCES_FILE" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
path = Path(sys.argv[2])
print((path if path.is_absolute() else root / path).resolve())
PY
  )"
  if [[ ! -f "$EVALUATOR_REPAIR_INSTANCES_FILE_ABS" ]]; then
    echo "ERROR: evaluator subset not found: $EVALUATOR_REPAIR_INSTANCES_FILE" >&2
    exit 2
  fi
  case "$EVALUATOR_REPAIR_INSTANCES_FILE_ABS" in "$REPO_ROOT"/*) ;; *)
    echo "ERROR: evaluator subset must be inside the repository" >&2; exit 2;; esac
  EVALUATOR_REPAIR_INSTANCES_FILE_REL="${EVALUATOR_REPAIR_INSTANCES_FILE_ABS#$REPO_ROOT/}"
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
if data.get("mode") != "polybench_pce":
    raise SystemExit("config mode must be polybench_pce")
root = path.parents[1] if path.parent.name == "configs" else Path.cwd()
def resolve(value):
    candidate = Path(os.path.expandvars(str(value))).expanduser()
    return candidate if candidate.is_absolute() else root / candidate
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
if [[ $FIXED_WORKTREE -eq 1 ]]; then
  case "$REMOTE_DIR" in "$HPC_ROOT"/staging/*) ;; *)
    echo "ERROR: --fixed-worktree requires an exact path under $HPC_ROOT/staging" >&2
    exit 2
  ;; esac
fi
CONFIG_SIF_CACHE_DIR="${CONFIG_SIF_CACHE_DIR//\$\{USER\}/$REMOTE_USER}"
REMOTE_APPTAINER_CACHE_DIR="${REMOTE_APPTAINER_CACHE_DIR:-$HPC_ROOT/shared/apptainer-cache}"
REMOTE_APPTAINER_TMP_DIR="${REMOTE_APPTAINER_TMP_DIR:-$HPC_ROOT/shared/apptainer-tmp}"
REMOTE_APPTAINER_SIF_CACHE_DIR="${REMOTE_APPTAINER_SIF_CACHE_DIR:-$CONFIG_SIF_CACHE_DIR}"

DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
RUN_REL="${RUN_DIR#$REPO_ROOT/}"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
REMOTE_DATASET="$REMOTE_DATASET_DIR/$DATASET_REL"
REMOTE_RUN="$REMOTE_RUN_DIR/$RUN_REL"
EVALUATOR_INSTANCE_ARGS=""
if [[ $EVALUATOR_REPAIR_INSTANCE_COUNT -gt 0 ]]; then
  for instance_id in "${EVALUATOR_REPAIR_INSTANCES[@]}"; do
    EVALUATOR_INSTANCE_ARGS+=" --instance-id $instance_id"
  done
fi
if [[ -n "$EVALUATOR_REPAIR_INSTANCES_FILE_REL" ]]; then
  EVALUATOR_INSTANCE_ARGS=" --instance-ids-file $EVALUATOR_REPAIR_INSTANCES_FILE_REL"
fi

if [[ $PREHEAT_HISTORY -eq 1 ]]; then
REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
export APPTAINER_CACHEDIR="$REMOTE_APPTAINER_CACHE_DIR"
export APPTAINER_TMPDIR="$REMOTE_APPTAINER_TMP_DIR"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$REMOTE_APPTAINER_SIF_CACHE_DIR"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR"
python3 -m scripts.tools.preheat_polybench_repository_history --config "$CONFIG_REL"
EOF
)
else
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
if [[ -n "$EVALUATOR_REPAIR_ID" ]]; then
  python3 scripts/resume_polybench_pce_evaluator.py \
    --config "$CONFIG_REL" --repair-id "$EVALUATOR_REPAIR_ID"$EVALUATOR_INSTANCE_ARGS
else
  python3 scripts/run_polybench_pce_hpc.py --config "$CONFIG_REL"
fi
EOF
)
fi

SUBMIT_LOCAL_DIR="$REPO_ROOT"
if [[ $FIXED_WORKTREE -eq 1 ]]; then
  # ulhpc-submit inspects --local-dir for requirements.txt even with --no-sync.
  # Use an empty metadata root so each controller slice cannot mutate the
  # shared user Python installation through automatic pip install --user.
  SUBMIT_LOCAL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polybench-submit-empty.XXXXXX")"
  cleanup_submit_local() {
    if [[ "$(basename "$SUBMIT_LOCAL_DIR")" == polybench-submit-empty.* ]]; then
      rm -rf -- "$SUBMIT_LOCAL_DIR"
    fi
  }
  trap cleanup_submit_local EXIT
fi
CMD=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$SUBMIT_LOCAL_DIR" --remote-dir "$REMOTE_DIR"
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
if [[ $FIXED_WORKTREE -eq 1 ]]; then
  CMD+=(--no-sync)
  REMOTE_CONNECTION="$(conda run --no-capture-output -n mini-swe python - "$ULHPC_CONFIG" <<'PY'
import sys
from pathlib import Path
import yaml
value = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
print(str(value.get("host", "access-iris.uni.lu")))
print(str(value.get("port", 8022)))
print(str(value.get("ssh_key", "")))
for item in value.get("sync_excludes") or []:
    print("exclude=" + str(item))
PY
)"
  REMOTE_HOST="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '1p')"
  REMOTE_PORT="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '2p')"
  REMOTE_KEY="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '3p')"
  REMOTE_KEY="${REMOTE_KEY/#\~/$HOME}"
  RSYNC_SSH="ssh -p $REMOTE_PORT"
  SSH_ARGS=(-p "$REMOTE_PORT")
  if [[ -n "$REMOTE_KEY" ]]; then
    RSYNC_SSH+=" -i $REMOTE_KEY"
    SSH_ARGS+=(-i "$REMOTE_KEY")
  fi
  SYNC_EXCLUDES=(--exclude configs/ulhpc_submit.yaml --exclude configs/ulhpc_submit_aion.yaml)
  while IFS= read -r pattern; do
    [[ -n "$pattern" ]] && SYNC_EXCLUDES+=(--exclude "$pattern")
  done < <(printf '%s\n' "$REMOTE_CONNECTION" | sed -n 's/^exclude=//p')
  if [[ $SUBMIT -eq 0 ]]; then
    echo "[polybench-pce-submit] dry-run fixed-worktree sync: $REMOTE_DIR"
  else
    ssh "${SSH_ARGS[@]}" "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $(printf '%q' "$REMOTE_DIR")"
    rsync -az --delete -e "$RSYNC_SSH" "${SYNC_EXCLUDES[@]}" \
      "$REPO_ROOT/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"
  fi
fi
[[ $SUBMIT -eq 0 ]] && CMD+=(--dry-run)
CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[polybench-pce-submit] mode=$([[ $SUBMIT -eq 1 ]] && echo submit || echo dry-run)"
echo "[polybench-pce-submit] config=$CONFIG_REL"
echo "[polybench-pce-submit] dataset=$DATASET_REL"
echo "[polybench-pce-submit] run=$RUN_REL"
echo "[polybench-pce-submit] evaluator_repair=${EVALUATOR_REPAIR_ID:-none}"
echo "[polybench-pce-submit] preheat_history=$PREHEAT_HISTORY"
echo "[polybench-pce-submit] evaluator_instances=${EVALUATOR_REPAIR_INSTANCES[*]:-all}"
echo "[polybench-pce-submit] evaluator_instances_file=${EVALUATOR_REPAIR_INSTANCES_FILE_REL:-none}"
echo "[polybench-pce-submit] controller_resources=$CPUS CPU/$MEM/$TIME_LIMIT"
"${CMD[@]}"
