#!/usr/bin/env bash
# Archived wrapper. Use scripts/run_batch.sh --checker-comparison.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

EXECUTE=0
DETACH=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    --detach) DETACH=1; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [[ $DETACH -eq 1 && $EXECUTE -ne 1 ]]; then
  echo "ERROR: --detach requires --execute" >&2
  exit 2
fi

if [[ $DETACH -eq 1 ]]; then
  SESSION="polybench-checker-comparison"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "ERROR: tmux session already exists: $SESSION" >&2
    exit 1
  fi
  DETACHED_CMD=(bash "$0" --execute)
  if [[ ${#ARGS[@]} -gt 0 ]]; then
    DETACHED_CMD+=("${ARGS[@]}")
  fi
  printf -v COMMAND '%q ' "${DETACHED_CMD[@]}"
  tmux new-session -d -s "$SESSION" "$COMMAND"
  echo "Started tmux session: $SESSION"
  echo "Monitor: tmux attach -t $SESSION"
  echo "Log: tail -f logs/checker_comparison_run.log"
  exit 0
fi

mkdir -p logs
CMD=(conda run -n mini-swe python scripts/internal/run_checker_comparison.py)
if [[ ${#ARGS[@]} -gt 0 ]]; then
  CMD+=("${ARGS[@]}")
fi
if [[ $EXECUTE -ne 1 ]]; then
  echo "Planning only. Pass --execute to start the four-arm comparison."
  exec "${CMD[@]}" --dry-run
fi

CAFFEINATE=()
if command -v caffeinate >/dev/null 2>&1; then
  CAFFEINATE=(caffeinate -i -s -d)
fi
"${CAFFEINATE[@]}" "${CMD[@]}" 2>&1 | tee -a logs/checker_comparison_run.log
