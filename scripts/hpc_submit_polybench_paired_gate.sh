#!/usr/bin/env bash
# Submit one resumable PolyBench C6 gate or feedback-free PCCE Controller slice.
set -euo pipefail

SUBMIT=0
CONFIG=""
JOB_NAME="polybench-paired-gate-controller"
REMOTE_DIR=""
REMOTE_RUN_DIR=""
ULHPC_CONFIG="configs/ulhpc_submit.yaml"
MEM="4G"
TIME_LIMIT="00:15:00"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --job-name) JOB_NAME="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --remote-run-dir) REMOTE_RUN_DIR="$2"; shift 2 ;;
    --ulhpc-config) ULHPC_CONFIG="$2"; shift 2 ;;
    --mem) MEM="$2"; shift 2 ;;
    --time) TIME_LIMIT="$2"; shift 2 ;;
    --fixed-worktree) shift ;;
    --submit) SUBMIT=1; shift ;;
    --dry-run) SUBMIT=0; shift ;;
    *) echo "ERROR: unknown paired-gate submit option: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -n "$CONFIG" && -n "$REMOTE_DIR" ]] || { echo "ERROR: --config and --remote-dir are required" >&2; exit 2; }
CONFIG_ABS="$(conda run --no-capture-output -n mini-swe python - "$REPO_ROOT" "$CONFIG" <<'PY'
import sys
from pathlib import Path
root, value = Path(sys.argv[1]), Path(sys.argv[2])
print((value if value.is_absolute() else root / value).resolve())
PY
)"
case "$CONFIG_ABS" in "$REPO_ROOT"/*) ;; *) echo "ERROR: config must be inside project" >&2; exit 2;; esac
[[ -f "$CONFIG_ABS" ]] || { echo "ERROR: config missing" >&2; exit 2; }

VALUES="$(conda run --no-capture-output -n mini-swe python - "$CONFIG_ABS" <<'PY'
import sys
from pathlib import Path
import yaml
path = Path(sys.argv[1])
raw = yaml.safe_load(path.read_text(encoding="utf-8"))
root = path.parents[1]
mode = raw.get("mode")
if mode == "polybench_pcce_paired_gate":
    source_paths = raw["paths"]
    controller = "gate"
elif mode == "polybench_pcce" and raw.get("pcce", {}).get("execution_mode") == "sampled_pcce":
    gate_path = root / raw["paths"]["gate_config"]
    gate = yaml.safe_load(gate_path.read_text(encoding="utf-8"))
    if gate.get("mode") != "polybench_pcce_paired_gate":
        raise SystemExit("sampled PCCE requires a paired-gate authority")
    source_paths = {**gate["paths"], "run_dir": raw["paths"]["run_dir"]}
    controller = "sampled_pcce"
else:
    raise SystemExit("paired-gate or sampled PCCE config mode required")
print(f"controller={controller}")
for key in ("source_snapshot", "image_manifest", "pce_run_manifest", "pce_outcomes", "run_dir"):
    value = Path(source_paths[key])
    if value.is_absolute():
        raise SystemExit(f"{key} must be project-relative")
    print(f"{key}={value}")
PY
)"
SOURCE_REL=""; IMAGE_REL=""; PCE_MANIFEST_REL=""; PCE_OUTCOMES_REL=""; RUN_REL=""; CONTROLLER=""
while IFS='=' read -r key value; do
  case "$key" in
    controller) CONTROLLER="$value" ;;
    source_snapshot) SOURCE_REL="$value" ;;
    image_manifest) IMAGE_REL="$value" ;;
    pce_run_manifest) PCE_MANIFEST_REL="$value" ;;
    pce_outcomes) PCE_OUTCOMES_REL="$value" ;;
    run_dir) RUN_REL="$value" ;;
  esac
done <<< "$VALUES"
[[ "$IMAGE_REL" == "$SOURCE_REL"/* ]] || { echo "ERROR: image manifest must be in source snapshot" >&2; exit 2; }
[[ "$(dirname "$PCE_MANIFEST_REL")" == "$(dirname "$PCE_OUTCOMES_REL")" ]] || { echo "ERROR: PCE files must share a run directory" >&2; exit 2; }
[[ -f "$REPO_ROOT/$SOURCE_REL/manifest.json" && -f "$REPO_ROOT/$IMAGE_REL" ]] || { echo "ERROR: local frozen source missing" >&2; exit 2; }
[[ -f "$REPO_ROOT/configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/eligibility.json" ]] || { echo "ERROR: frozen eligibility missing" >&2; exit 2; }

ULHPC_CONFIG_ABS="$REPO_ROOT/$ULHPC_CONFIG"
[[ -f "$ULHPC_CONFIG_ABS" ]] || { echo "ERROR: ULHPC config missing" >&2; exit 2; }
REMOTE_CONNECTION="$(conda run --no-capture-output -n mini-swe python - "$ULHPC_CONFIG_ABS" <<'PY'
import sys
from pathlib import Path
import yaml
raw = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
for key in ("user", "host", "port", "ssh_key"):
    print(raw.get(key, ""))
for pattern in raw.get("sync_excludes") or []:
    print("exclude=" + str(pattern))
PY
)"
REMOTE_USER="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '1p')"
REMOTE_HOST="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '2p')"
REMOTE_PORT="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '3p')"
REMOTE_KEY="$(printf '%s\n' "$REMOTE_CONNECTION" | sed -n '4p')"
[[ -n "$REMOTE_USER" && -n "$REMOTE_HOST" && -n "$REMOTE_PORT" ]] || { echo "ERROR: incomplete ULHPC connection" >&2; exit 2; }
REMOTE_KEY="${REMOTE_KEY/#\~/$HOME}"
HPC_ROOT="/scratch/users/$REMOTE_USER/vibe-coding-planning"
REMOTE_RUN_DIR="${REMOTE_RUN_DIR:-$HPC_ROOT/run_state}"
[[ "$REMOTE_RUN_DIR" == "$HPC_ROOT/run_state" ]] || { echo "ERROR: paired-gate run state must use $HPC_ROOT/run_state" >&2; exit 2; }
case "$REMOTE_DIR" in "$HPC_ROOT"/staging/*) ;; *) echo "ERROR: staging path must be under $HPC_ROOT/staging" >&2; exit 2;; esac
REMOTE_DATASET="$HPC_ROOT/datasets/$SOURCE_REL"
REMOTE_PCE_RUN="$REMOTE_RUN_DIR/$(dirname "$PCE_MANIFEST_REL")"
REMOTE_RUN="$REMOTE_RUN_DIR/$RUN_REL"
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"
LOCAL_GIT_HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
PCE_REL_DIR="$(dirname "$PCE_MANIFEST_REL")"
PCE_REL_PARENT="$(dirname "$PCE_REL_DIR")"

ULHPC_SUBMIT_BIN="${ULHPC_SUBMIT_BIN:-}"
if [[ -z "$ULHPC_SUBMIT_BIN" ]] && command -v ulhpc-submit >/dev/null 2>&1; then
  ULHPC_SUBMIT_BIN="$(command -v ulhpc-submit)"
fi
if [[ -z "$ULHPC_SUBMIT_BIN" && -n "${CONDA_EXE:-}" ]]; then
  ULHPC_SUBMIT_BIN="$(dirname "$CONDA_EXE")/ulhpc-submit"
fi
[[ -x "$ULHPC_SUBMIT_BIN" ]] || { echo "ERROR: ulhpc-submit unavailable" >&2; exit 127; }

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
export APPTAINER_CACHEDIR="$HPC_ROOT/shared/apptainer-cache"
export APPTAINER_TMPDIR="$HPC_ROOT/shared/apptainer-tmp"
export ULHPC_APPTAINER_SIF_CACHE_DIR="$HPC_ROOT/shared/sif-cache"
export VIBE_PROJECT_GIT_HEAD="$LOCAL_GIT_HEAD"
mkdir -p "\$APPTAINER_CACHEDIR" "\$APPTAINER_TMPDIR" "$PCE_REL_PARENT"
if [[ ! -e "$PCE_REL_DIR" && ! -L "$PCE_REL_DIR" ]]; then
  ln -s "$REMOTE_PCE_RUN" "$PCE_REL_DIR"
fi
test "\$(readlink "$PCE_REL_DIR")" = "$REMOTE_PCE_RUN"
test -f "$PCE_MANIFEST_REL" && test -f "$PCE_OUTCOMES_REL"
set +x
source "\$HOME/.config/vibe-coding-planning/deepseek.env"
test -n "\${DEEPSEEK_API_KEY:-}" || exit 2
if [[ "$CONTROLLER" == "sampled_pcce" ]]; then
  python3 scripts/run_polybench_pcce_hpc.py --config "$CONFIG_REL"
else
  python3 -m src.polybench_pcce.paired_gate --config "$CONFIG_REL"
fi
EOF
)

SUBMIT_LOCAL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polybench-gate-submit-empty.XXXXXX")"
cleanup_submit_local() { [[ "$(basename "$SUBMIT_LOCAL_DIR")" == polybench-gate-submit-empty.* ]] && rm -rf -- "$SUBMIT_LOCAL_DIR"; }
trap cleanup_submit_local EXIT

RSYNC_SSH="ssh -p $REMOTE_PORT"
SSH_ARGS=(-p "$REMOTE_PORT")
if [[ -n "$REMOTE_KEY" ]]; then RSYNC_SSH+=" -i $REMOTE_KEY"; SSH_ARGS+=(-i "$REMOTE_KEY"); fi
SYNC_EXCLUDES=(--exclude configs/ulhpc_submit.yaml --exclude configs/ulhpc_submit_aion.yaml)
while IFS= read -r pattern; do [[ -n "$pattern" ]] && SYNC_EXCLUDES+=(--exclude "$pattern"); done < <(printf '%s\n' "$REMOTE_CONNECTION" | sed -n 's/^exclude=//p')
if [[ $SUBMIT -eq 1 ]]; then
  [[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ]] || { echo "ERROR: submit requires a clean worktree" >&2; exit 2; }
  ssh "${SSH_ARGS[@]}" "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $(printf '%q' "$REMOTE_DIR")"
  rsync -az --delete -e "$RSYNC_SSH" "${SYNC_EXCLUDES[@]}" "$REPO_ROOT/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/"
fi

CMD=("$ULHPC_SUBMIT_BIN" --submit-only --json --local-dir "$SUBMIT_LOCAL_DIR" --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME" --partition batch --cpus 1 --mem "$MEM" --time "$TIME_LIMIT" --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --stage-data "$REPO_ROOT/$SOURCE_REL:$REMOTE_DATASET" --link-as "$SOURCE_REL"
  --persistent-output "$RUN_REL:$REMOTE_RUN"
  --apptainer-cache-dir "$HPC_ROOT/shared/apptainer-cache"
  --apptainer-tmp-dir "$HPC_ROOT/shared/apptainer-tmp"
  --apptainer-sif-cache-dir "$HPC_ROOT/shared/sif-cache"
  --remote-ignore-extra --config "$ULHPC_CONFIG_ABS" --no-sync)
[[ $SUBMIT -eq 0 ]] && CMD+=(--dry-run)
CMD+=(-- bash -c "$REMOTE_SCRIPT")
echo "[polybench-paired-gate] mode=$([[ $SUBMIT -eq 1 ]] && echo submit || echo dry-run) config=$CONFIG_REL run=$RUN_REL"
"${CMD[@]}"
