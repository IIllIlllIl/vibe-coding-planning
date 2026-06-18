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
MEM="8G"
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
  --mem SIZE                Memory (default: 8G)
  --gpus N                  GPUs (default: 0)
  --remote-dir DIR          Remote project directory for ulhpc-submit
  --ulhpc-config FILE       ulhpc-submit config file
                            (default: configs/ulhpc_submit.yaml if present)
  --user USER               UL HPC username
  --host HOST               UL HPC access host
  --port PORT               UL HPC SSH port
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

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[vibe-hpc-smoke] started at \$(date)"
echo "[vibe-hpc-smoke] host=\$(hostname)"
echo "[vibe-hpc-smoke] pwd=\$(pwd)"
echo "[vibe-hpc-smoke] python=\$(command -v python)"
python --version
python - <<'PY'
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

command -v docker
docker --version
docker info >/tmp/vibe_hpc_docker_info.txt
sed -n '1,40p' /tmp/vibe_hpc_docker_info.txt
docker image ls --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | sed -n '1,20p'

python -m src.environment.docker_env maintain \
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
  ULHPC_CMD+=(--port "$ULHPC_PORT_ARG")
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
