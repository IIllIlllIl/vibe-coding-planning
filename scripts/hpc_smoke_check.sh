#!/usr/bin/env bash
# Smoke-check the hpc_submit/ulhpc-submit path before designing full batch submit.
#
# Default mode runs ulhpc-submit --dry-run, checks ULHPC SSH connectivity, and
# does not submit a Slurm job. Add --submit to actually run the smoke on HPC.
set -euo pipefail

SUBMIT=0
SYNC_DRY_RUN=0
JOB_NAME="vibe-hpc-smoke"
PARTITION="batch"
TIME_LIMIT="00:30:00"
CPUS="1"
MEM="4G"
GPUS="0"
REMOTE_DIR=""
CONDA_ENV="mini-swe"
LOCAL_DIR=""
FULL_LOGS=0
CHECK_API_KEY=0
DOCKER_MAX_CACHED_IMAGES="4"
DOCKER_MIN_FREE_GB="5"
ULHPC_CONFIG=""
ULHPC_USER_ARG=""
ULHPC_HOST_ARG=""
ULHPC_PORT_ARG=""
SKIP_PORT_CHECK=0
SKIP_SSH_CHECK=0
SSH_CONNECT_TIMEOUT="15"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/hpc_smoke_check.sh [options]

Default: dry-run only. It still checks ULHPC SSH connectivity through
ulhpc-submit, but does not submit a Slurm job. Add --submit to submit.

Options:
  --submit                  Submit the smoke job. Without this, use --dry-run.
  --sync-dry-run            In dry-run mode, also let ulhpc-submit attempt its
                            rsync dry-run. By default dry-run adds --no-sync,
                            while still checking SSH connectivity.
  --job-name NAME           Slurm job name (default: vibe-hpc-smoke)
  --partition NAME          Slurm partition (default: batch)
  --time HH:MM:SS           Wall time (default: 00:30:00)
  --cpus N                  CPUs per task (default: 1)
  --mem SIZE                Memory (default: 4G)
  --gpus N                  GPUs (default: 0)
  --remote-dir DIR          Remote project directory for ulhpc-submit
  --ulhpc-config FILE       ulhpc-submit config file
                            (default: configs/ulhpc_submit.yaml if present)
  --user USER               UL HPC username
  --host HOST               UL HPC access host
  --port PORT               UL HPC SSH port (exported as ULHPC_PORT)
  --skip-port-check         Skip local TCP preflight before ulhpc-submit
  --skip-ssh-check          Skip one-shot SSH preflight before ulhpc-submit
  --ssh-connect-timeout N   Seconds for one-shot SSH preflight (default: 15)
  --conda-env NAME          Conda env to activate on HPC (default: mini-swe)
  --local-dir DIR           Local dir to sync (default: repo root)
  --full-logs               Ask ulhpc-submit to download full remote logs
  --check-api-key           Require DEEPSEEK_API_KEY to be present remotely
  --docker-max-cached N     Image cache smoke limit (default: 4)
  --docker-min-free-gb N    Docker free-space smoke threshold (default: 5)
  -h, --help                Show this help

Examples:
  bash scripts/hpc_smoke_check.sh
  bash scripts/hpc_smoke_check.sh --submit --remote-dir '~/hpc_runs/vibe-smoke'
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --submit)
      SUBMIT=1
      shift
      ;;
    --sync-dry-run)
      SYNC_DRY_RUN=1
      shift
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
    --ulhpc-config)
      ULHPC_CONFIG="$2"
      shift 2
      ;;
    --user)
      ULHPC_USER_ARG="$2"
      shift 2
      ;;
    --host)
      ULHPC_HOST_ARG="$2"
      shift 2
      ;;
    --port)
      ULHPC_PORT_ARG="$2"
      shift 2
      ;;
    --skip-port-check)
      SKIP_PORT_CHECK=1
      shift
      ;;
    --skip-ssh-check)
      SKIP_SSH_CHECK=1
      shift
      ;;
    --ssh-connect-timeout)
      SSH_CONNECT_TIMEOUT="$2"
      shift 2
      ;;
    --conda-env)
      CONDA_ENV="$2"
      shift 2
      ;;
    --local-dir)
      LOCAL_DIR="$2"
      shift 2
      ;;
    --full-logs)
      FULL_LOGS=1
      shift
      ;;
    --check-api-key)
      CHECK_API_KEY=1
      shift
      ;;
    --docker-max-cached)
      DOCKER_MAX_CACHED_IMAGES="$2"
      shift 2
      ;;
    --docker-min-free-gb)
      DOCKER_MIN_FREE_GB="$2"
      shift 2
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

if ! [[ "$CPUS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --cpus must be a positive integer" >&2
  exit 2
fi
if ! [[ "$GPUS" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --gpus must be a non-negative integer" >&2
  exit 2
fi
if ! [[ "$DOCKER_MAX_CACHED_IMAGES" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --docker-max-cached must be a positive integer" >&2
  exit 2
fi
if ! [[ "$DOCKER_MIN_FREE_GB" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --docker-min-free-gb must be a positive integer" >&2
  exit 2
fi
if ! [[ "$SSH_CONNECT_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --ssh-connect-timeout must be a positive integer" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "$LOCAL_DIR" ]]; then
  LOCAL_DIR="$REPO_ROOT"
fi
if [[ -z "$ULHPC_CONFIG" && -f "$REPO_ROOT/configs/ulhpc_submit.yaml" ]]; then
  ULHPC_CONFIG="$REPO_ROOT/configs/ulhpc_submit.yaml"
fi

if ! command -v ulhpc-submit >/dev/null 2>&1; then
  echo "ERROR: ulhpc-submit not found." >&2
  echo "Install the adjacent hpc_submit project first:" >&2
  echo "  cd ../../hpc_submit && pip install -e \".[dev]\"" >&2
  exit 127
fi

if [[ -z "${ULHPC_LOG_DIR:-}" ]]; then
  export ULHPC_LOG_DIR="$REPO_ROOT/.tmp_hpc_smoke/ulhpc-runs"
fi
mkdir -p "$ULHPC_LOG_DIR"

CONFIG_HOST=""
CONFIG_PORT=""
CONFIG_USER=""
CONFIG_SSH_KEY=""
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

PREFLIGHT_HOST="${ULHPC_HOST_ARG:-${ULHPC_HOST:-${CONFIG_HOST:-access-iris.uni.lu}}}"
PREFLIGHT_PORT="${ULHPC_PORT_ARG:-${ULHPC_PORT:-${CONFIG_PORT:-8022}}}"
PREFLIGHT_USER="${ULHPC_USER_ARG:-${ULHPC_USER:-${CONFIG_USER:-}}}"
PREFLIGHT_SSH_KEY="${ULHPC_SSH_KEY:-${CONFIG_SSH_KEY:-}}"
if [[ "$PREFLIGHT_SSH_KEY" == "~/"* ]]; then
  PREFLIGHT_SSH_KEY="$HOME/${PREFLIGHT_SSH_KEY#\~/}"
fi

if [[ -n "$ULHPC_PORT_ARG" ]]; then
  export ULHPC_PORT="$ULHPC_PORT_ARG"
fi

if [[ "$SKIP_PORT_CHECK" -eq 0 ]]; then
  if command -v nc >/dev/null 2>&1; then
    echo "[hpc-smoke] checking tcp connectivity: $PREFLIGHT_HOST:$PREFLIGHT_PORT"
    if ! nc -z -w 10 "$PREFLIGHT_HOST" "$PREFLIGHT_PORT"; then
      echo "ERROR: cannot connect to $PREFLIGHT_HOST:$PREFLIGHT_PORT" >&2
      echo "Check VPN/campus network, ULHPC access host/port, or use --skip-port-check to let ulhpc-submit try anyway." >&2
      exit 3
    fi
  else
    echo "[hpc-smoke] nc not found; skipping tcp preflight"
  fi
fi

if [[ "$SKIP_SSH_CHECK" -eq 0 ]]; then
  if [[ -z "$PREFLIGHT_USER" ]]; then
    echo "ERROR: cannot run SSH preflight without ULHPC user." >&2
    echo "Set user in configs/ulhpc_submit.yaml, ULHPC_USER, or --user." >&2
    exit 2
  fi
  if command -v ssh >/dev/null 2>&1; then
    SSH_PREFLIGHT_CMD=(
      ssh
      -p "$PREFLIGHT_PORT"
      -o BatchMode=yes
      -o NumberOfPasswordPrompts=0
      -o PreferredAuthentications=publickey
      -o ConnectTimeout="$SSH_CONNECT_TIMEOUT"
    )
    if [[ -n "$PREFLIGHT_SSH_KEY" ]]; then
      SSH_PREFLIGHT_CMD+=(
        -o IdentitiesOnly=yes
        -i "$PREFLIGHT_SSH_KEY"
      )
    fi
    SSH_PREFLIGHT_CMD+=("$PREFLIGHT_USER@$PREFLIGHT_HOST" true)

    echo "[hpc-smoke] checking one-shot ssh auth: $PREFLIGHT_USER@$PREFLIGHT_HOST:$PREFLIGHT_PORT"
    if ! "${SSH_PREFLIGHT_CMD[@]}"; then
      echo "ERROR: one-shot SSH preflight failed for $PREFLIGHT_USER@$PREFLIGHT_HOST:$PREFLIGHT_PORT" >&2
      echo "Not invoking ulhpc-submit, to avoid its current multi-attempt Paramiko retry loop." >&2
      echo "After VPN/access/SSH key is stable, rerun this script; use --skip-ssh-check only when you explicitly want ulhpc-submit to try." >&2
      exit 4
    fi
  else
    echo "[hpc-smoke] ssh not found; skipping ssh preflight"
  fi
fi

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[vibe-hpc-smoke] started at \$(date)"
echo "[vibe-hpc-smoke] host=\$(hostname)"
echo "[vibe-hpc-smoke] pwd=\$(pwd)"
if command -v module >/dev/null 2>&1; then
  module load miniconda3 2>/dev/null || true
fi
if command -v conda >/dev/null 2>&1; then
  set +u
  source "\$(conda info --base)/etc/profile.d/conda.sh"
  set -u
  conda activate "$CONDA_ENV"
fi
EASYBUILD_PYTHON="/opt/apps/easybuild/systems/iris/rhel810-20250803/2023b/broadwell/software/Python/3.11.5-GCCcore-13.2.0/bin/python"
if [[ -x "\$EASYBUILD_PYTHON" ]]; then
  PYTHON_BIN="\$EASYBUILD_PYTHON"
else
  PYTHON_BIN="\$(command -v python3.11 || command -v python)"
fi
echo "[vibe-hpc-smoke] python=\$PYTHON_BIN"
"\$PYTHON_BIN" --version
"\$PYTHON_BIN" - <<'PY'
import importlib
import sys

checks = [
    ("minisweagent", "1.17.5"),
    ("swebench", None),
    ("yaml", None),
    ("docker", None),
]
for name, expected in checks:
    module = importlib.import_module(name)
    version = getattr(module, "__version__", "unknown")
    print(f"[vibe-hpc-smoke] import {name}: {version}")
    if expected is not None and version != expected:
        raise SystemExit(f"{name} version mismatch: expected {expected}, got {version}")
print(f"[vibe-hpc-smoke] sys.executable={sys.executable}")
PY

if [[ "$CHECK_API_KEY" == "1" ]]; then
  test -n "\${DEEPSEEK_API_KEY:-}" || {
    echo "DEEPSEEK_API_KEY is required but not present in the HPC job environment" >&2
    exit 20
  }
  echo "[vibe-hpc-smoke] DEEPSEEK_API_KEY is present"
else
  if [[ -n "\${DEEPSEEK_API_KEY:-}" ]]; then
    echo "[vibe-hpc-smoke] DEEPSEEK_API_KEY is present"
  else
    echo "[vibe-hpc-smoke] DEEPSEEK_API_KEY is not set; API-dependent jobs will need it"
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker CLI is not available on this HPC node." >&2
  echo "[vibe-hpc-smoke] container runtime diagnostics:" >&2
  command -v apptainer >&2 || true
  command -v singularity >&2 || true
  echo "Traceback: Docker CLI unavailable for this project smoke." >&2
  exit 30
fi
docker --version
if ! docker info >/tmp/vibe_hpc_docker_info.txt; then
  echo "ERROR: Docker daemon is not reachable from this HPC job." >&2
  echo "Traceback: Docker daemon unavailable for this project smoke." >&2
  exit 31
fi
sed -n '1,40p' /tmp/vibe_hpc_docker_info.txt
docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | sed -n '1,20p'

"\$PYTHON_BIN" -m src.environment.docker_env maintain \
  --max-cached-images "$DOCKER_MAX_CACHED_IMAGES" \
  --max-concurrent 1 \
  --min-free-gb "$DOCKER_MIN_FREE_GB"

echo "[vibe-hpc-smoke] completed at \$(date)"
EOF
)

ULHPC_CMD=(
  ulhpc-submit
  --local-dir "$LOCAL_DIR"
  --job-name "$JOB_NAME"
  --partition "$PARTITION"
  --cpus "$CPUS"
  --mem "$MEM"
  --time "$TIME_LIMIT"
  --gpus "$GPUS"
  --conda-env "$CONDA_ENV"
)

if [[ -n "$ULHPC_CONFIG" ]]; then
  ULHPC_CMD+=(--config "$ULHPC_CONFIG")
fi
if [[ -n "$ULHPC_USER_ARG" ]]; then
  ULHPC_CMD+=(--user "$ULHPC_USER_ARG")
fi
if [[ -n "$ULHPC_HOST_ARG" ]]; then
  ULHPC_CMD+=(--host "$ULHPC_HOST_ARG")
fi
if [[ -n "$ULHPC_PORT_ARG" ]]; then
  :
fi
if [[ -n "$REMOTE_DIR" ]]; then
  ULHPC_CMD+=(--remote-dir "$REMOTE_DIR")
fi
if [[ "$FULL_LOGS" -eq 1 ]]; then
  ULHPC_CMD+=(--full-logs)
fi
if [[ "$SUBMIT" -eq 0 ]]; then
  ULHPC_CMD+=(--dry-run)
  if [[ "$SYNC_DRY_RUN" -eq 0 ]]; then
    ULHPC_CMD+=(--no-sync)
  fi
fi

ULHPC_CMD+=(-- bash -lc "$REMOTE_SCRIPT")

echo "[hpc-smoke] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[hpc-smoke] local-dir=$LOCAL_DIR"
if [[ -n "$REMOTE_DIR" ]]; then
  echo "[hpc-smoke] remote-dir=$REMOTE_DIR"
fi
exec "${ULHPC_CMD[@]}"
