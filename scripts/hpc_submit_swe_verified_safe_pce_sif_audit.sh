#!/usr/bin/env bash
# Freeze exact SIF bytes and verify declared base commits for formal Safe PCE.
set -euo pipefail

SUBMIT=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT_REL="output/SWE-bench_Verified/swe-verified-pce-inputs/20260831_verified500_91aa3ed5"
SELECTION_REL="configs/frozen_swe_verified_safe_pce/verified482-formal-v1-20260914/selection.json"
AUDIT_REL="output/SWE-bench_Verified/sif-audits/verified482-formal-v1-20260914"
CONFIG_SOURCE="$ROOT/configs/ulhpc_submit.yaml"

for arg in "$@"; do
  case "$arg" in
    --submit) SUBMIT=1 ;;
    --dry-run) SUBMIT=0 ;;
    -h|--help)
      echo "Usage: bash scripts/hpc_submit_swe_verified_safe_pce_sif_audit.sh [--submit|--dry-run]"
      exit 0 ;;
    *) echo "ERROR: unknown option: $arg" >&2; exit 2 ;;
  esac
done

test -f "$ROOT/$SNAPSHOT_REL/manifest.json" || {
  echo "ERROR: fixed Verified source snapshot is missing" >&2; exit 2;
}
test -f "$ROOT/$SELECTION_REL" || {
  echo "ERROR: formal482 selection is missing" >&2; exit 2;
}
test -f "$CONFIG_SOURCE" || {
  echo "ERROR: worktree-local ULHPC config is missing" >&2; exit 2;
}

ULHPC_SUBMIT_BIN="${ULHPC_SUBMIT_BIN:-$(command -v ulhpc-submit || true)}"
test -x "$ULHPC_SUBMIT_BIN" || {
  echo "ERROR: ulhpc-submit not found" >&2; exit 127;
}
REMOTE_USER="$(conda run -n mini-swe python -c 'import os,sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print(os.environ.get("ULHPC_USER") or d.get("user") or "")' "$CONFIG_SOURCE")"
test -n "$REMOTE_USER" || {
  echo "ERROR: ULHPC user is unavailable" >&2; exit 2;
}

CONFIG="$(mktemp /tmp/vibe-verified482-sif-audit-ulhpc.XXXXXX.yaml)"
cp "$CONFIG_SOURCE" "$CONFIG"
chmod 600 "$CONFIG"
STAGE_ROOT="$(mktemp -d /tmp/vibe-verified482-sif-audit-stage.XXXXXX)"
mkdir -p \
  "$STAGE_ROOT/scripts/tools" \
  "$STAGE_ROOT/$(dirname "$SELECTION_REL")"
cp "$ROOT/scripts/tools/freeze_swe_verified_sif_manifest.py" \
  "$STAGE_ROOT/scripts/tools/freeze_swe_verified_sif_manifest.py"
cp "$ROOT/$SELECTION_REL" "$STAGE_ROOT/$SELECTION_REL"
trap 'rm -rf "$STAGE_ROOT"; rm -f "$CONFIG"' EXIT

HPC_ROOT="/scratch/users/$REMOTE_USER/vibe-coding-planning"
REMOTE_DATASET="$HPC_ROOT/datasets/swe-verified500-v1-20260831"
REMOTE_AUDIT="$HPC_ROOT/operations/swe-verified482-sif-audit-v1-20260914"
mkdir -p "$ROOT/$AUDIT_REL"

REMOTE_COMMAND=$(cat <<EOF
set -euo pipefail
python3 scripts/tools/freeze_swe_verified_sif_manifest.py \
  --source-snapshot "$SNAPSHOT_REL" \
  --selection-manifest "$SELECTION_REL" \
  --sif-cache-dir "$HPC_ROOT/shared/sif-cache" \
  --output "$AUDIT_REL/images.json" \
  --verify-base-commits \
  --require-complete \
  --apptainer-bin apptainer
EOF
)

COMMAND=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$STAGE_ROOT"
  --remote-dir "$HPC_ROOT/runs/swe-verified482-sif-audit-v1-20260914"
  --job-name swe-verified482-sif-audit
  --partition batch --nodes 1 --ntasks 1 --cpus 1 --mem 4G --time 02:00:00 --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --stage-data "$ROOT/$SNAPSHOT_REL:$REMOTE_DATASET" --link-as "$SNAPSHOT_REL"
  --persistent-output "$AUDIT_REL:$REMOTE_AUDIT"
  --remote-ignore-extra --config "$CONFIG"
)
[[ "$SUBMIT" -eq 0 ]] && COMMAND+=(--dry-run)
COMMAND+=(-- bash -c "$REMOTE_COMMAND")

echo "[verified482-sif-audit] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[verified482-sif-audit] scope=482 selected cached images; exact SHA-256 and declared base-commit presence"
echo "[verified482-sif-audit] resources=1cpu/4G/02:00:00"
"${COMMAND[@]}"
