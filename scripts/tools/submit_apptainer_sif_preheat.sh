#!/usr/bin/env bash
# Submit a Slurm job that pre-pulls all Apptainer SIF images required by a GEPA
# config. This is intended to run on an HPC login node where sbatch is available.
#
# Usage (from the HPC login node, inside the project directory):
#   bash scripts/tools/submit_apptainer_sif_preheat.sh \
#     --config configs/gepa_verified_rules_formal_pilot_apptainer.yaml \
#     --sif-cache-dir /scratch/users/twang/vibe-sif-cache
#
# The script will create the cache directory if it does not exist, then submit
# a single-node batch job that runs prepare_apptainer_sifs.py.
set -euo pipefail

CONFIG=""
SIF_CACHE_DIR=""
JOB_NAME="vibe-preheat-sifs"
PARTITION="batch"
CPUS="1"
MEM="8G"
TIME="2-00:00:00"
TIMEOUT="1800"
MAX_ATTEMPTS="3"
RETRY_BACKOFF="60"

usage() {
  cat <<'USAGE'
Usage:
  bash scripts/tools/submit_apptainer_sif_preheat.sh --config PATH --sif-cache-dir DIR [options]

Required:
  --config PATH          GEPA config file (relative to repo root)
  --sif-cache-dir DIR    Directory on a shared HPC filesystem to store .sif files

Slurm options:
  --job-name NAME        Job name (default: vibe-preheat-sifs)
  --partition NAME       Slurm partition (default: batch)
  --cpus N               CPUs per task (default: 1)
  --mem SIZE             Memory (default: 8G)
  --time HH:MM:SS        Wall time (default: 2-00:00:00, i.e. 2 days)
  --timeout SECONDS      Timeout per SIF pull attempt (default: 1800)
  --max-attempts N       Attempts per missing SIF image (default: 3)
  --retry-backoff SEC    Seconds between failed pull attempts (default: 60)

Examples:
  bash scripts/tools/submit_apptainer_sif_preheat.sh \
    --config configs/gepa_verified_rules_formal_pilot_apptainer.yaml \
    --sif-cache-dir /scratch/users/twang/vibe-sif-cache
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG="$2"
      shift 2
      ;;
    --sif-cache-dir)
      SIF_CACHE_DIR="$2"
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
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --max-attempts)
      MAX_ATTEMPTS="$2"
      shift 2
      ;;
    --retry-backoff)
      RETRY_BACKOFF="$2"
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

if [[ -z "$CONFIG" ]]; then
  echo "ERROR: --config is required" >&2
  usage >&2
  exit 2
fi
if [[ -z "$SIF_CACHE_DIR" ]]; then
  echo "ERROR: --sif-cache-dir is required" >&2
  usage >&2
  exit 2
fi
if ! [[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --timeout must be a positive integer" >&2
  exit 2
fi
if ! [[ "$MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: --max-attempts must be a positive integer" >&2
  exit 2
fi
if ! [[ "$RETRY_BACKOFF" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --retry-backoff must be a non-negative integer" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_ABS="$(cd "$REPO_ROOT" && python3 -c "from pathlib import Path; print(Path('$CONFIG').resolve())")"
if [[ ! -f "$CONFIG_ABS" ]]; then
  echo "ERROR: config not found: $CONFIG" >&2
  exit 2
fi

# Relative path inside the repo so the remote job can reference it directly.
CONFIG_REL="${CONFIG_ABS#$REPO_ROOT/}"

mkdir -p "$SIF_CACHE_DIR"

JOB_SCRIPT=$(cat <<EOF
#!/bin/bash
#SBATCH --job-name=$JOB_NAME
#SBATCH --partition=$PARTITION
#SBATCH --cpus-per-task=$CPUS
#SBATCH --mem=$MEM
#SBATCH --time=$TIME
#SBATCH --output=$SIF_CACHE_DIR/preheat_%j.out
#SBATCH --error=$SIF_CACHE_DIR/preheat_%j.err

set -euo pipefail

source /etc/profile.d/modules.sh
module load lang/Python/3.11 tools/Apptainer

cd "$REPO_ROOT"
echo "[preheat] started at \$(date) on \$(hostname)"
echo "[preheat] config=$CONFIG_REL"
echo "[preheat] sif_cache_dir=$SIF_CACHE_DIR"
echo "[preheat] timeout=$TIMEOUT"
echo "[preheat] max_attempts=$MAX_ATTEMPTS"
echo "[preheat] retry_backoff=$RETRY_BACKOFF"

python3 scripts/tools/prepare_apptainer_sifs.py --config "$CONFIG_REL" --sif-cache-dir "$SIF_CACHE_DIR" --timeout "$TIMEOUT" --max-attempts "$MAX_ATTEMPTS" --retry-backoff "$RETRY_BACKOFF" --failed-output "$SIF_CACHE_DIR/preheat_failed_images_\${SLURM_JOB_ID}.txt"

RC=\$?
echo "[preheat] finished with rc=\$RC at \$(date)"
exit \$RC
EOF
)

JOB_FILE="$SIF_CACHE_DIR/preheat_job_script.sh"
echo "$JOB_SCRIPT" > "$JOB_FILE"
chmod +x "$JOB_FILE"

echo "[preheat] submitting Slurm job..."
JOB_ID=$(sbatch "$JOB_FILE" | awk '{print $NF}')
echo "[preheat] submitted job $JOB_ID"
echo "[preheat] logs will be at $SIF_CACHE_DIR/preheat_${JOB_ID}.out"
