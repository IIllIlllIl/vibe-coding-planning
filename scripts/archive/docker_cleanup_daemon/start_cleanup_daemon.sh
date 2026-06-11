#!/usr/bin/env bash
# ARCHIVED: launcher for the retired temporary Docker cleanup daemon.
#
# Features:
#   - Single-instance protection (cannot start multiple daemons)
#   - Graceful shutdown with timeout
#   - Status reporting
#   - Log management
#
# Usage:
#   bash scripts/start_cleanup_daemon.sh              # Start (default: 2 hour interval)
#   bash scripts/start_cleanup_daemon.sh --interval 1800  # 30 minutes
#   bash scripts/start_cleanup_daemon.sh --stop       # Stop gracefully
#   bash scripts/start_cleanup_daemon.sh --kill       # Kill immediately
#   bash scripts/start_cleanup_daemon.sh --attach     # Attach to running session
#   bash scripts/start_cleanup_daemon.sh --status     # Show status

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SESSION_NAME="docker-cleanup-daemon"
DAEMON_SCRIPT="$REPO_ROOT/scripts/archive/docker_cleanup_daemon/daemon_cleanup_dangling_images.sh"
LOG_FILE="$REPO_ROOT/logs/daemon_cleanup_dangling.log"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.cache/vibe-docker-cleanup}"
PID_FILE="$RUNTIME_DIR/daemon.pid"

mkdir -p "$RUNTIME_DIR"

find_daemon_pid() {
  pgrep -f '[d]aemon_cleanup_dangling_images.sh' 2>/dev/null | head -1
}

# Ensure scripts exist
if [[ ! -f "$DAEMON_SCRIPT" ]]; then
  echo "ERROR: Daemon script not found: $DAEMON_SCRIPT" >&2
  exit 1
fi

# Parse command
COMMAND="${1:-start}"
case "$COMMAND" in
  --stop) COMMAND="stop" ;;
  --kill) COMMAND="kill" ;;
  --attach) COMMAND="attach" ;;
  --status) COMMAND="status" ;;
esac
if [[ $# -gt 0 ]]; then
  shift
fi
DAEMON_ARGS=("$@")

case "$COMMAND" in
  start)
    # Check if already running
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Daemon already running: $SESSION_NAME"
      echo ""
      echo "Options:"
      echo "  Attach:   tmux attach -t $SESSION_NAME"
      echo "  Stop:     bash $0 --stop"
      echo "  Status:   bash $0 --status"
      exit 0
    fi
    EXISTING_PID="$(find_daemon_pid)"
    if [[ -n "$EXISTING_PID" ]]; then
      echo "Daemon already running outside tmux: pid=$EXISTING_PID"
      echo "Stop: bash $0 --stop"
      exit 0
    fi

    # Build command
    DAEMON_CMD=(bash "$DAEMON_SCRIPT" "${DAEMON_ARGS[@]}")
    printf -v DAEMON_CMD_QUOTED '%q ' "${DAEMON_CMD[@]}"

    # Start with caffeinate (macOS) or directly (Linux)
    if command -v caffeinate >/dev/null 2>&1; then
      FULL_CMD="caffeinate -i -s -d $DAEMON_CMD_QUOTED"
    else
      FULL_CMD="$DAEMON_CMD_QUOTED"
    fi

    mkdir -p "$(dirname "$LOG_FILE")"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting daemon: $SESSION_NAME"
    tmux new-session -d -s "$SESSION_NAME" "$FULL_CMD"

    # Wait a moment for daemon to start and acquire lock
    sleep 0.5

    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "ERROR: Failed to start daemon session" >&2
      exit 1
    fi

    echo "✓ Daemon started in tmux session: $SESSION_NAME"
    echo "  Interval: ${DAEMON_ARGS[*]:-2 hours (default)}"
    echo "  Log: $LOG_FILE"
    echo ""
    echo "Monitor:"
    echo "  tail -f $LOG_FILE"
    echo "  bash $0 --attach"
    echo ""
    echo "Stop gracefully:"
    echo "  bash $0 --stop"
    echo ""
    ;;

  stop)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Sending graceful shutdown signal to $SESSION_NAME..."
      tmux send-keys -t "$SESSION_NAME" C-c

      # Wait for graceful exit (up to 10 seconds)
      for i in {1..10}; do
        if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
          echo "✓ Daemon stopped gracefully"
          exit 0
        fi
        sleep 1
      done

      echo "WARNING: Daemon did not exit after 10s. Killing session..."
      tmux kill-session -t "$SESSION_NAME"
      echo "✓ Daemon killed"
      exit 0
    fi

    EXISTING_PID="$(find_daemon_pid)"
    if [[ -z "$EXISTING_PID" ]]; then
      echo "Daemon not running: $SESSION_NAME"
      exit 0
    fi
    echo "Sending graceful shutdown signal to daemon pid=$EXISTING_PID..."
    kill -TERM "$EXISTING_PID"
    echo "✓ Stop signal sent"
    ;;

  kill)
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Daemon not running: $SESSION_NAME"
      exit 0
    fi
    tmux kill-session -t "$SESSION_NAME"
    echo "✓ Daemon killed immediately"
    ;;

  attach)
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "ERROR: Daemon not running: $SESSION_NAME"
      exit 1
    fi
    tmux attach -t "$SESSION_NAME"
    ;;

  status)
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "✓ Daemon is running: $SESSION_NAME"
      echo ""
      echo "Recent logs (last 15 lines):"
      echo "---"
      if [[ -f "$LOG_FILE" ]]; then
        tail -15 "$LOG_FILE"
      else
        echo "(no logs yet)"
      fi
      echo "---"
    elif EXISTING_PID="$(find_daemon_pid)" && [[ -n "$EXISTING_PID" ]]; then
      echo "✓ Daemon is running outside tmux: pid=$EXISTING_PID"
      echo ""
      echo "Recent logs (last 15 lines):"
      echo "---"
      tail -15 "$LOG_FILE" 2>/dev/null || echo "(no logs yet)"
      echo "---"
    else
      echo "✗ Daemon not running: $SESSION_NAME"
      echo ""
      if [[ -f "$LOG_FILE" ]]; then
        echo "Last run logs:"
        tail -5 "$LOG_FILE"
      fi
    fi
    ;;

  *)
    echo "Usage: bash $0 [start|stop|kill|attach|status] [--interval SECONDS]"
    echo ""
    echo "Commands:"
    echo "  start [--interval 7200]  Start daemon (default: 2 hours)"
    echo "  stop                     Stop gracefully (waits up to 10s)"
    echo "  kill                     Stop immediately"
    echo "  attach                   Attach to tmux session"
    echo "  status                   Show daemon status and recent logs"
    echo ""
    exit 1
    ;;
esac
