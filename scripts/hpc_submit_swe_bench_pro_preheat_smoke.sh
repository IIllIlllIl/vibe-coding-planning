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
MEMORY="4G"
ALL_MISSING=0
SUBMIT=0

usage() {
  cat <<'USAGE'
Usage: bash scripts/hpc_submit_swe_bench_pro_preheat_smoke.sh [options]

Options:
  --instance-id ID       One exact request from the frozen quick25 manifest
  --all-missing          Visit all frozen requests and pull only missing SIFs
  --mem SIZE             Slurm memory (default: 4G)
  --time HH:MM:SS        Slurm wall time (default: 02:00:00)
  --submit               Submit; default is an ulhpc-submit dry-run
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --all-missing) ALL_MISSING=1; shift ;;
    --mem) MEMORY="$2"; shift 2 ;;
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

REQUEST_COMMANDS=$(conda run -n mini-swe python -c '
import json
import shlex
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rows = manifest["requests"] if sys.argv[3] == "1" else [
    row for row in manifest["requests"] if row["instance_id"] == sys.argv[2]
]
if not rows or (sys.argv[3] == "0" and len(rows) != 1):
    raise SystemExit("instance_id must identify exactly one frozen quick25 request")
for row in rows:
    image = row["image_ref"]
    sif = row["sif_filename"]
    if not image.startswith("jefzda/sweap-images:"):
        raise SystemExit("unexpected image ref")
    if not sif.endswith(".sif") or "/" in sif:
        raise SystemExit("unsafe SIF filename")
    print(shlex.join(["pull_one", image, sif, row["instance_id"]]))
' "$MANIFEST" "$INSTANCE_ID" "$ALL_MISSING")
REQUEST_COUNT=$(printf '%s\n' "$REQUEST_COMMANDS" | wc -l | tr -d ' ')
FIRST_IMAGE=$(printf '%s\n' "$REQUEST_COMMANDS" | sed -n '1s/^pull_one \([^ ]*\).*/\1/p')

if [[ "$ALL_MISSING" -eq 1 ]]; then
  REMOTE_DIR="/scratch/users/twang/vibe-coding-planning/runs/swe-bench-pro-preheat-node-tmp-recovery-v1-20260906"
  JOB_NAME="pro-q25-node-tmp-recovery"
fi

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail
echo "[pro-preheat-smoke] started_at=\$(date --iso-8601=seconds) host=\$(hostname) job_id=\${SLURM_JOB_ID}"
NODE_TMP="/tmp/vibe-pro-preheat-\${SLURM_JOB_ID}"
PARTIAL_SIF=""
cleanup() {
  rm -rf "\$NODE_TMP"
  if [[ -n "\$PARTIAL_SIF" ]]; then rm -f "\$PARTIAL_SIF"; fi
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
cached=0
pulled=0
failures=0
pull_one() {
  image_ref="\$1"
  sif_name="\$2"
  instance_id="\$3"
  final_sif="$SIF_CACHE/\$sif_name"
  PARTIAL_SIF="$SIF_CACHE/.\$sif_name.partial.\${SLURM_JOB_ID}"
  if [[ -f "\$final_sif" ]]; then
    apptainer inspect "\$final_sif" >/dev/null
    cached=\$((cached + 1))
    echo "[pro-preheat-smoke] cached instance_id=\$instance_id bytes=\$(stat -c %s "\$final_sif")"
    return
  fi
  echo "[pro-preheat-smoke] pulling instance_id=\$instance_id image=\$image_ref"
  if ! apptainer pull "\$PARTIAL_SIF" "docker://\$image_ref"; then
    failures=\$((failures + 1))
    echo "[pro-preheat-smoke] failed instance_id=\$instance_id" >&2
    rm -f "\$PARTIAL_SIF"
    find "\$NODE_TMP" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    PARTIAL_SIF=""
    return
  fi
  apptainer inspect "\$PARTIAL_SIF" >/dev/null
  echo "[pro-preheat-smoke] sif_sha256=\$(sha256sum "\$PARTIAL_SIF" | awk '{print \$1}') instance_id=\$instance_id"
  echo "[pro-preheat-smoke] sif_bytes=\$(stat -c %s "\$PARTIAL_SIF") instance_id=\$instance_id"
  mv "\$PARTIAL_SIF" "\$final_sif"
  PARTIAL_SIF=""
  pulled=\$((pulled + 1))
}
$REQUEST_COMMANDS
echo "[pro-preheat-smoke] summary cached=\$cached pulled=\$pulled failed=\$failures requested=$REQUEST_COUNT"
echo "[pro-preheat-smoke] completed_at=\$(date --iso-8601=seconds)"
if [[ "\$failures" -ne 0 ]]; then exit 1; fi
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
  --mem "$MEMORY"
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
echo "[pro-preheat-smoke] selection=$([[ "$ALL_MISSING" -eq 1 ]] && echo all-missing || echo "$INSTANCE_ID")"
echo "[pro-preheat-smoke] request_count=$REQUEST_COUNT first_image=$FIRST_IMAGE"
echo "[pro-preheat-smoke] resources=1cpu/$MEMORY/$WALLTIME"
exec "${ULHPC_CMD[@]}"
