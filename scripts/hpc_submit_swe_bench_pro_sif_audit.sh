#!/usr/bin/env bash
# Audit quick25 SIF bytes, base commits, and exposed non-ancestor history.
set -euo pipefail

SUBMIT=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT_REL="output/SWE-bench_Pro/pce-inputs/quick25-v1-20260904"
AUDIT_REL="output/SWE-bench_Pro/image-audit/quick25-v1-20260906"
CONFIG_SOURCE="$ROOT/configs/ulhpc_submit.yaml"
for arg in "$@"; do
  case "$arg" in
    --submit) SUBMIT=1 ;;
    --dry-run) SUBMIT=0 ;;
    -h|--help)
      echo "Usage: bash scripts/hpc_submit_swe_bench_pro_sif_audit.sh [--submit|--dry-run]"
      exit 0 ;;
    *) echo "ERROR: unknown option: $arg" >&2; exit 2 ;;
  esac
done

test -f "$ROOT/$SNAPSHOT_REL/manifest.json" || {
  echo "ERROR: prepared Pro source snapshot is missing" >&2; exit 2;
}
test -f "$CONFIG_SOURCE" || { echo "ERROR: worktree-local ULHPC config is missing" >&2; exit 2; }
ULHPC_SUBMIT_BIN="${ULHPC_SUBMIT_BIN:-$(command -v ulhpc-submit || true)}"
test -x "$ULHPC_SUBMIT_BIN" || { echo "ERROR: ulhpc-submit not found" >&2; exit 127; }
REMOTE_USER="$(conda run -n mini-swe python -c 'import os,sys,yaml; d=yaml.safe_load(open(sys.argv[1])) or {}; print(os.environ.get("ULHPC_USER") or d.get("user") or "")' "$CONFIG_SOURCE")"
test -n "$REMOTE_USER" || { echo "ERROR: ULHPC user is unavailable" >&2; exit 2; }
CONFIG="$(mktemp /tmp/vibe-pro-sif-audit-ulhpc.XXXXXX.yaml)"
cp "$CONFIG_SOURCE" "$CONFIG"
chmod 600 "$CONFIG"
STAGE_ROOT="$(mktemp -d /tmp/vibe-pro-sif-audit-stage.XXXXXX)"
mkdir -p "$STAGE_ROOT/scripts/tools"
cp "$ROOT/scripts/tools/freeze_swe_bench_pro_sif_manifest.py" \
  "$STAGE_ROOT/scripts/tools/freeze_swe_bench_pro_sif_manifest.py"
trap 'rm -rf "$STAGE_ROOT"; rm -f "$CONFIG"' EXIT
HPC_ROOT="/scratch/users/$REMOTE_USER/vibe-coding-planning"
REMOTE_DATASET="$HPC_ROOT/datasets/swe-bench-pro-quick25-v1-20260904"
REMOTE_AUDIT="$HPC_ROOT/operations/swe-bench-pro-quick25-pce-audit-v1-20260906"
mkdir -p "$ROOT/$AUDIT_REL"

REMOTE_COMMAND=$(cat <<EOF
set -euo pipefail
python3 scripts/tools/freeze_swe_bench_pro_sif_manifest.py \
  --source-snapshot "$SNAPSHOT_REL" \
  --sif-cache-dir "$HPC_ROOT/shared/sif-cache" \
  --output "$AUDIT_REL/images.json" \
  --apptainer-bin apptainer
EOF
)

COMMAND=(
  "$ULHPC_SUBMIT_BIN" --submit-only --json
  --local-dir "$STAGE_ROOT"
  --remote-dir "$HPC_ROOT/runs/swe-bench-pro-quick25-pce-audit-v1-20260906"
  --job-name swe-bench-pro-q25-sif-audit
  --partition batch --nodes 1 --ntasks 1 --cpus 1 --mem 4G --time 00:10:00 --gpus 0
  --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda
  --stage-data "$ROOT/$SNAPSHOT_REL:$REMOTE_DATASET" --link-as "$SNAPSHOT_REL"
  --persistent-output "$AUDIT_REL:$REMOTE_AUDIT"
  --remote-ignore-extra --config "$CONFIG"
)
[[ "$SUBMIT" -eq 0 ]] && COMMAND+=(--dry-run)
COMMAND+=(-- bash -c "$REMOTE_COMMAND")

echo "[pro-sif-audit] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[pro-sif-audit] scope=25 images; base commit plus non-ancestor history census"
echo "[pro-sif-audit] resources=1cpu/4G/00:10:00"
"${COMMAND[@]}"
