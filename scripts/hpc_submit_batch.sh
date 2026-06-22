#!/usr/bin/env bash
# Submit a GEPA rules optimization job to ULHPC via ulhpc-submit.
#
# This script wraps ulhpc-submit for GEPA-specific needs:
#   - stages the dataset snapshot (output/ is excluded from ulhpc-submit sync)
#   - submits the GEPA CLI via conda run
#   - retrieves the run_dir output after the job finishes
#
# Default mode is dry-run; pass --submit to actually submit.
#
# Usage:
#   bash scripts/hpc_submit_batch.sh \
#     --gepa-rules \
#     --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
#     --remote-dir '~/hpc_runs/vibe-coding-planning' \
#     --job-name gepa-ref-smoke \
#     --time 02:00:00 \
#     --cpus 1 \
#     --mem 16G \
#     --remote-env-file '~/.config/vibe-coding-planning/deepseek.env' \
#     --submit
set -euo pipefail

SUBMIT=0
GEPA_RULES=0
GEPA_CONFIG=""
JOB_NAME="vibe-gepa"
PARTITION="batch"
TIME_LIMIT="02:00:00"
CPUS="1"
MEM="16G"
GPUS="0"
REMOTE_DIR=""
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
  --cpus N                  CPUs per task (default: 1)
  --mem SIZE                Memory (default: 16G)
  --gpus N                  GPUs (default: 0)
  --remote-dir DIR          Remote project directory on HPC
  --remote-env-file FILE    Remote shell env file sourced inside the Slurm job
                            (default: ~/.config/vibe-coding-planning/deepseek.env)
  --ulhpc-config FILE       ulhpc-submit config file
                            (default: configs/ulhpc_submit.yaml if present)
  --full-logs               Download full remote logs instead of tailing
  --submit                  Actually submit the job (default is dry-run)

Examples:
  # Dry-run: print the commands that would run
  bash scripts/hpc_submit_batch.sh \
    --gepa-rules \
    --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml

  # Real submission
  bash scripts/hpc_submit_batch.sh \
    --gepa-rules \
    --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
    --remote-dir '~/hpc_runs/vibe-gepa-reflection-smoke' \
    --job-name gepa-ref-smoke \
    --time 02:00:00 \
    --cpus 1 \
    --mem 16G \
    --remote-env-file '~/.config/vibe-coding-planning/deepseek.env' \
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
      # Default mode; accept explicitly for convenience.
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
if [[ -z "$REMOTE_ENV_FILE" ]]; then
  echo "ERROR: --remote-env-file must not be empty" >&2
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

if ! command -v ulhpc-submit >/dev/null 2>&1; then
  echo "ERROR: ulhpc-submit not found. Install the adjacent hpc_submit project:" >&2
  echo "  cd ../../hpc_submit && pip install -e \".[dev]\"" >&2
  exit 127
fi

# ---------------------------------------------------------------------------
# Parse ulhpc-submit config for SSH connection details
# ---------------------------------------------------------------------------
CONFIG_HOST=""
CONFIG_PORT=""
CONFIG_USER=""
CONFIG_SSH_KEY=""
if [[ -n "$ULHPC_CONFIG" && -f "$ULHPC_CONFIG" ]]; then
  while IFS='=' read -r CONFIG_KEY CONFIG_VALUE; do
    case "$CONFIG_KEY" in
      host) CONFIG_HOST="$CONFIG_VALUE" ;;
      port) CONFIG_PORT="$CONFIG_VALUE" ;;
      user) CONFIG_USER="$CONFIG_VALUE" ;;
      ssh_key) CONFIG_SSH_KEY="$CONFIG_VALUE" ;;
    esac
  done < <(python - "$ULHPC_CONFIG" <<'PY'
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else None
data = {}
if path and path.exists():
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
for key in ("host", "port", "user", "ssh_key"):
    print(f"{key}={data.get(key, '')}")
PY
  )
fi

PREFLIGHT_HOST="${ULHPC_HOST:-${CONFIG_HOST:-access-iris.uni.lu}}"
PREFLIGHT_PORT="${ULHPC_PORT:-${CONFIG_PORT:-8022}}"
PREFLIGHT_USER="${ULHPC_USER:-${CONFIG_USER:-}}"
PREFLIGHT_SSH_KEY="${ULHPC_SSH_KEY:-${CONFIG_SSH_KEY:-}}"
if [[ "$PREFLIGHT_SSH_KEY" == "~/"* ]]; then
  PREFLIGHT_SSH_KEY="$HOME/${PREFLIGHT_SSH_KEY#\~/}"
fi

if [[ -z "$PREFLIGHT_USER" ]]; then
  echo "ERROR: cannot determine ULHPC user. Set user in configs/ulhpc_submit.yaml or ULHPC_USER." >&2
  exit 2
fi

if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="~/hpc_runs/vibe-coding-planning"
fi

# ---------------------------------------------------------------------------
# Read GEPA config to locate dataset snapshot and run_dir
# ---------------------------------------------------------------------------
GEPA_PATHS=$(python - "$GEPA_CONFIG_ABS" <<'PY'
import sys
from pathlib import Path
import yaml

config_path = Path(sys.argv[1])
root = config_path.parents[1] if config_path.parent.name == "configs" else Path.cwd()
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
paths = cfg.get("paths", {})

def resolve(raw):
    if not raw:
        return ""
    candidate = Path(raw)
    return str(candidate if candidate.is_absolute() else root / candidate)

print(f"dataset_snapshot={resolve(paths.get('dataset_snapshot', ''))}")
print(f"run_dir={resolve(paths.get('run_dir', ''))}")
print(f"initial_rules={resolve(paths.get('initial_rules', ''))}")
PY
)

DATASET_SNAPSHOT=""
RUN_DIR=""
INITIAL_RULES=""
while IFS='=' read -r KEY VALUE; do
  case "$KEY" in
    dataset_snapshot) DATASET_SNAPSHOT="$VALUE" ;;
    run_dir) RUN_DIR="$VALUE" ;;
    initial_rules) INITIAL_RULES="$VALUE" ;;
  esac
done <<< "$GEPA_PATHS"

if [[ -z "$DATASET_SNAPSHOT" || ! -d "$DATASET_SNAPSHOT" ]]; then
  echo "ERROR: dataset_snapshot directory not found locally: $DATASET_SNAPSHOT" >&2
  echo "Build it first, e.g.:" >&2
  echo "  conda run -n mini-swe python scripts/tools/build_gepa_pilot_dataset.py" >&2
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

# Relative paths inside the remote project directory
DATASET_REL="${DATASET_SNAPSHOT#$REPO_ROOT/}"
RUN_DIR_REL="${RUN_DIR#$REPO_ROOT/}"
GEPA_CONFIG_REL="${GEPA_CONFIG_ABS#$REPO_ROOT/}"

# ---------------------------------------------------------------------------
# Build remote command
# ---------------------------------------------------------------------------
REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[vibe-gepa] started at \$(date) on \$(hostname)"
source /etc/profile.d/modules.sh
module load tools/Apptainer
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
# Ensure the vendored gepa package is installed in the remote environment.
python3 -m pip install --quiet --user -e third_party/gepa || true
python3 scripts/internal/run_gepa_rules.py --config "$GEPA_CONFIG_REL"
GEPA_RC=\$?
echo "[vibe-gepa] GEPA exited with rc=\$GEPA_RC at \$(date)"
exit \$GEPA_RC
EOF
)

# ---------------------------------------------------------------------------
# Build ulhpc-submit command
# ---------------------------------------------------------------------------
ULHPC_CMD=(
  ulhpc-submit
  --local-dir "$REPO_ROOT"
  --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME"
  --partition "$PARTITION"
  --cpus "$CPUS"
  --mem "$MEM"
  --time "$TIME_LIMIT"
  --gpus "$GPUS"
  --conda-env mini-swe
)

if [[ -n "$ULHPC_CONFIG" ]]; then
  ULHPC_CMD+=(--config "$ULHPC_CONFIG")
fi
if [[ "$FULL_LOGS" -eq 1 ]]; then
  ULHPC_CMD+=(--full-logs)
fi
if [[ "$SUBMIT" -eq 0 ]]; then
  ULHPC_CMD+=(--dry-run --no-sync)
fi

ULHPC_CMD+=(-- bash -lc "$REMOTE_SCRIPT")

# ---------------------------------------------------------------------------
# SSH / rsync helper
# ---------------------------------------------------------------------------
SSH_OPTS=(
  -p "$PREFLIGHT_PORT"
)
if [[ -n "$PREFLIGHT_SSH_KEY" ]]; then
  SSH_OPTS+=(-i "$PREFLIGHT_SSH_KEY")
fi
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------
echo "[hpc-submit] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[hpc-submit] gepa-config=$GEPA_CONFIG"
echo "[hpc-submit] remote-dir=$REMOTE_DIR"
echo "[hpc-submit] remote-env-file=$REMOTE_ENV_FILE"
echo "[hpc-submit] dataset_snapshot=$DATASET_SNAPSHOT"
echo "[hpc-submit] run_dir=$RUN_DIR"

if [[ "$SUBMIT" -eq 0 ]]; then
  echo "[hpc-submit] dry-run: dataset snapshot would be rsynced to remote"
  echo "[hpc-submit] dry-run: the following ulhpc-submit command would be executed:"
  printf '  %s' "${ULHPC_CMD[*]}"
  echo
  echo "[hpc-submit] dry-run: remote script:"
  echo "$REMOTE_SCRIPT"
  exit 0
fi

# Ensure remote directory exists
ssh "${SSH_OPTS[@]}" "$PREFLIGHT_USER@$PREFLIGHT_HOST" "mkdir -p $(printf '%q' "$REMOTE_DIR")"

# Stage dataset snapshot (output/ is excluded from ulhpc-submit sync)
echo "[hpc-submit] staging dataset snapshot to HPC..."
rsync -avz -e "$RSYNC_SSH" \
  "$DATASET_SNAPSHOT/" \
  "$PREFLIGHT_USER@$PREFLIGHT_HOST:$(printf '%q' "$REMOTE_DIR/$DATASET_REL")/"

# Submit job
echo "[hpc-submit] submitting GEPA job..."
set +e
"${ULHPC_CMD[@]}"
ULHPC_RC=$?
set -e

# Retrieve run_dir output regardless of success/failure
echo "[hpc-submit] retrieving run_dir output (rc=$ULHPC_RC)..."
mkdir -p "$(dirname "$RUN_DIR")"
rsync -avz -e "$RSYNC_SSH" \
  "$PREFLIGHT_USER@$PREFLIGHT_HOST:$(printf '%q' "$REMOTE_DIR/$RUN_DIR_REL")/" \
  "$RUN_DIR/"

if [[ $ULHPC_RC -ne 0 ]]; then
  echo "[hpc-submit] ulhpc-submit reported failure; run_dir has been retrieved for inspection" >&2
fi

exit "$ULHPC_RC"
