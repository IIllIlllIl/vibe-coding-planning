#!/usr/bin/env bash
# ARCHIVED: temporary daemon retained only for historical reproducibility.
# Docker maintenance is now owned by DockerCapacityWindow.
#
# Features:
#   - Process lock (prevents multiple instances)
#   - Graceful shutdown (SIGTERM/SIGINT)
#   - Log rotation (10MB threshold)
#   - macOS compatible (no perl regex)
#   - Disk usage tracking
#   - Safe cleanup (never touches running/stopped container images)
#
# Usage:
#   bash scripts/daemon_cleanup_dangling_images.sh [--interval SECONDS]
#   bash scripts/daemon_cleanup_dangling_images.sh --interval 7200  # 2 hours (default)
#
# To run detached in tmux with caffeinate (macOS):
#   tmux new-session -d -s docker-cleanup \
#     'caffeinate -i -s -d bash scripts/daemon_cleanup_dangling_images.sh'

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

# Config
INTERVAL_SECONDS=7200  # 2 hours
MAX_CONCURRENT="${DOCKER_MAX_CONCURRENT:-3}"
MAX_CACHED_IMAGES="${DOCKER_MAX_CACHED_IMAGES:-6}"
MIN_FREE_GB="${DOCKER_MIN_FREE_GB:-20}"
LOG_FILE="logs/daemon_cleanup_dangling.log"
LOG_MAX_SIZE=10485760  # 10MB
# Use user-specific temp dir for security (not world-writable /tmp)
RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.cache/vibe-docker-cleanup}"
LOCK_DIR="$RUNTIME_DIR/daemon.lock"
EXIT_FLAG_FILE="$RUNTIME_DIR/daemon.exit"

mkdir -p "$RUNTIME_DIR" "$(dirname "$LOG_FILE")"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval)
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Atomic lock using mkdir (POSIX-compatible)
acquire_lock() {
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "ERROR: Another instance is already running"
    return 1
  fi
  return 0
}

release_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

# Log rotation: keep current log, archive old when size exceeds threshold
rotate_log_if_needed() {
  if [[ -f "$LOG_FILE" ]]; then
    local size=0
    if command -v stat >/dev/null 2>&1; then
      # macOS: stat -f%z, Linux: stat -c%s
      size=$(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    fi
    if [[ $size -gt $LOG_MAX_SIZE ]]; then
      mv "$LOG_FILE" "$LOG_FILE.$(date +%s)"
      log "Log rotated (previous size: $((size / 1024 / 1024))MB)"
    fi
  fi
}

# Helper: get disk free space in GB (safe extraction)
get_disk_free() {
  local free_kb=$(df -Pk . | awk 'NR==2 {print $4}')
  if [[ -z "$free_kb" ]]; then
    echo 0
  else
    echo $((free_kb / 1024 / 1024))
  fi
}

# Helper: extract pruned count (macOS-compatible)
extract_pruned_count() {
  local log_file="$1"
  if grep -q 'Deleted Images:' "$log_file" 2>/dev/null; then
    # macOS-safe: grep followed by grep -oE (not -oP)
    grep 'Deleted Images:' "$log_file" | grep -oE '[0-9]+' | tail -1
  else
    echo "?"
  fi
}

# Graceful shutdown handler
shutdown_handler() {
  log "=== SIGTERM/SIGINT received. Graceful shutdown ==="
  touch "$EXIT_FLAG_FILE"
}

trap shutdown_handler SIGTERM SIGINT
trap 'release_lock' EXIT

# Acquire lock or exit
if ! acquire_lock; then
  exit 1
fi

rotate_log_if_needed
log "=== Daemon started: interval=${INTERVAL_SECONDS}s, pid=$$ ==="

# Main loop
iteration=0
while true; do
  iteration=$((iteration + 1))

  # Check exit flag
  if [[ -f "$EXIT_FLAG_FILE" ]]; then
    log "Exit flag detected. Stopping."
    rm -f "$EXIT_FLAG_FILE"
    break
  fi

  log "[Iteration $iteration] Starting cleanup..."
  START_TIME=$(date +%s)

  # Get initial state
  DISK_BEFORE=$(get_disk_free)
  IMAGE_COUNT_BEFORE=$(docker images --format "{{.ID}}" 2>/dev/null | wc -l | tr -d ' ')
  # Count dangling images (safely handle errors)
  DANGLING_BEFORE=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c '^<none>:<none>$' 2>/dev/null)
  DANGLING_BEFORE=${DANGLING_BEFORE:-0}  # Default to 0 if empty

  log "  Disk before: ${DISK_BEFORE}GiB free"
  log "  Total images: $IMAGE_COUNT_BEFORE"
  log "  Dangling images: $DANGLING_BEFORE"

  # All cleanup mutations use the same inter-process maintenance lock and
  # reference-aware policy as pipeline/checker workers.
  log "  [1/3] Running centralized Docker maintenance..."
  if conda run -n mini-swe python -m src.environment.docker_env maintain \
      --max-concurrent "$MAX_CONCURRENT" \
      --max-cached-images "$MAX_CACHED_IMAGES" \
      --min-free-gb "$MIN_FREE_GB" \
      > /tmp/docker_prune.log 2>&1; then
    log "    Centralized maintenance completed"
  else
    log "    WARNING: centralized Docker maintenance failed"
    cat /tmp/docker_prune.log >> "$LOG_FILE" 2>/dev/null || true
  fi

  log "  [2/3] Image and BuildKit policy handled by DockerCapacityWindow"

  # Get final state
  DISK_AFTER=$(get_disk_free)
  IMAGE_COUNT_AFTER=$(docker images --format "{{.ID}}" 2>/dev/null | wc -l | tr -d ' ')
  # Count dangling images (safely handle errors)
  DANGLING_AFTER=$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c '^<none>:<none>$' 2>/dev/null)
  DANGLING_AFTER=${DANGLING_AFTER:-0}  # Default to 0 if empty
  FREED=$((DISK_AFTER - DISK_BEFORE))

  # Step 3: Report
  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))

  log "  [3/3] Cleanup complete (${ELAPSED}s)"
  log "    Disk after: ${DISK_AFTER}GiB free (freed ${FREED}GiB)"
  log "    Total images: $IMAGE_COUNT_AFTER (removed $((IMAGE_COUNT_BEFORE - IMAGE_COUNT_AFTER)))"
  log "    Dangling images: $DANGLING_AFTER (removed $((DANGLING_BEFORE - DANGLING_AFTER)))"

  # Clean up temp files
  rm -f /tmp/docker_prune.log

  # Check exit flag again before sleeping
  if [[ -f "$EXIT_FLAG_FILE" ]]; then
    log "Exit flag detected. Stopping."
    rm -f "$EXIT_FLAG_FILE"
    break
  fi

  # Sleep before next iteration (interruptible)
  log "[Iteration $((iteration + 1))] Waiting ${INTERVAL_SECONDS}s until next cleanup..."
  sleep "$INTERVAL_SECONDS" &
  SLEEP_PID=$!
  wait $SLEEP_PID 2>/dev/null || true
done

log "=== Daemon stopped ==="
