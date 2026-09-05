#!/usr/bin/env bash
# Submit one SWE-bench Pro SIF pull using compute-node-local Apptainer scratch.
set -euo pipefail

MANIFEST="configs/frozen_swe_bench_pro_quick25/v1-20260904/image-request-manifest.json"
INSTANCE_ID="instance_internetarchive__openlibrary-5069b09e5f64428dce59b33455c8bb17fe577070-v8717e18970bcdc4e0d2cea3b1527752b21e74866"
ULHPC_CONFIG="configs/ulhpc_submit.yaml"
REMOTE_DIR="/scratch/users/twang/vibe-coding-planning/runs/swe-bench-pro-preheat-node-tmp-smoke-v1-20260906"
SIF_CACHE="/scratch/users/twang/vibe-coding-planning/shared/sif-cache"
APPTAINER_CACHE="/scratch/users/twang/vibe-coding-planning/operations/swe-bench-pro-python-quick25-v1-20260904/apptainer-cache"
LOCK_FILE="/scratch/users/twang/vibe-coding-planning/shared/sif-cache/.single-writer-preheat.lock"
JOB_NAME="pro-q25-node-tmp-smoke"
WALLTIME="02:00:00"
SUBMIT=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/hpc_submit_swe_bench_pro_preheat_smoke.sh [options]

Options:
  --instance-id ID       One exact request from the frozen quick25 manifest
  --time HH:MM:SS        Slurm wall time (default: 02:00:00)
  --submit               Submit; default is an ulhpc-submit dry-run
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --time) WALLTIME="$2"; shift 2 ;;
    --submit) SUBMIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
test -f "$MANIFEST" || { echo "ERROR: frozen manifest not found" >&2; exit 2; }
test -f "$ULHPC_CONFIG" || { echo "ERROR: worktree-local ULHPC config not found" >&2; exit 2; }
EMPTY_PROJECT="$REPO_ROOT/.tmp_hpc_smoke/pro-preheat-empty-project"
mkdir -p "$EMPTY_PROJECT"

REQUEST_VALUES=$(conda run -n mini-swe python -c '
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
matches = [row for row in manifest["requests"] if row["instance_id"] == sys.argv[2]]
if len(matches) != 1:
    raise SystemExit("instance_id must identify exactly one frozen quick25 request")
row = matches[0]
print(row["image_ref"])
print(row["sif_filename"])
' "$MANIFEST" "$INSTANCE_ID")
IMAGE_REF=$(printf '%s\n' "$REQUEST_VALUES" | sed -n '1p')
SIF_NAME=$(printf '%s\n' "$REQUEST_VALUES" | sed -n '2p')
[[ "$IMAGE_REF" == jefzda/sweap-images:* ]] || { echo "ERROR: unexpected image ref" >&2; exit 2; }
[[ "$SIF_NAME" == *.sif && "$SIF_NAME" != */* ]] || { echo "ERROR: unsafe SIF filename" >&2; exit 2; }

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[pro-preheat-smoke] started_at=\$(date --iso-8601=seconds) host=\$(hostname) job_id=\${SLURM_JOB_ID}"
NODE_TMP="/tmp/vibe-pro-preheat-\${SLURM_JOB_ID}"
FINAL_SIF="$SIF_CACHE/$SIF_NAME"
PARTIAL_SIF="$SIF_CACHE/.$SIF_NAME.partial.\${SLURM_JOB_ID}"
cleanup() {
  rm -rf "\$NODE_TMP"
  rm -f "\$PARTIAL_SIF"
}
trap cleanup EXIT
mkdir -p "\$NODE_TMP" "$SIF_CACHE" "$APPTAINER_CACHE"
chmod 700 "\$NODE_TMP"
export APPTAINER_TMPDIR="\$NODE_TMP"
export APPTAINER_CACHEDIR="$APPTAINER_CACHE"
echo "[pro-preheat-smoke] node_tmp=\$NODE_TMP"
df -h "\$NODE_TMP"
df -i "\$NODE_TMP"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "[pro-preheat-smoke] single-writer lock busy" >&2; exit 75; }
if [[ -f "\$FINAL_SIF" ]]; then
  apptainer inspect "\$FINAL_SIF" >/dev/null
  echo "[pro-preheat-smoke] already_available bytes=\$(stat -c %s "\$FINAL_SIF")"
  exit 0
fi
apptainer pull "\$PARTIAL_SIF" "docker://$IMAGE_REF"
apptainer inspect "\$PARTIAL_SIF" >/dev/null
echo "[pro-preheat-smoke] sif_sha256=\$(sha256sum "\$PARTIAL_SIF" | awk '{print \$1}')"
echo "[pro-preheat-smoke] sif_bytes=\$(stat -c %s "\$PARTIAL_SIF")"
mv "\$PARTIAL_SIF" "\$FINAL_SIF"
echo "[pro-preheat-smoke] completed_at=\$(date --iso-8601=seconds) final_sif=\$FINAL_SIF"
EOF
)

ULHPC_CMD=(
  ulhpc-submit
  --submit-only
  --json
  --config "$ULHPC_CONFIG"
  --local-dir "$EMPTY_PROJECT"
  --no-sync
  --remote-dir "$REMOTE_DIR"
  --job-name "$JOB_NAME"
  --partition batch
  --nodes 1
  --ntasks 1
  --cpus 1
  --mem 4G
  --time "$WALLTIME"
  --gpus 0
  --module tools/Apptainer
  --python python3
  --no-conda
  --apptainer-cache-dir "$APPTAINER_CACHE"
  --apptainer-sif-cache-dir "$SIF_CACHE"
)
if [[ "$SUBMIT" -eq 0 ]]; then
  ULHPC_CMD+=(--dry-run)
fi
ULHPC_CMD+=(-- bash -c "$REMOTE_SCRIPT")

echo "[pro-preheat-smoke] mode=$([[ "$SUBMIT" -eq 1 ]] && echo submit || echo dry-run)"
echo "[pro-preheat-smoke] instance_id=$INSTANCE_ID"
echo "[pro-preheat-smoke] image_ref=$IMAGE_REF"
echo "[pro-preheat-smoke] resources=1cpu/4G/$WALLTIME"
exec "${ULHPC_CMD[@]}"
