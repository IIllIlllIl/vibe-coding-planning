#!/usr/bin/env bash
# Prepared small SIF acquisition; default mode performs read-only planning.
set -euo pipefail
MODE="${1:---dry-run}"
if [[ "$MODE" != "--dry-run" && "$MODE" != "--run" ]]; then
  echo "Usage: $0 [--dry-run|--run]" >&2
  exit 2
fi
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
conda run -n mini-swe python -c 'import hashlib,json; from pathlib import Path; from scripts.tools.login_apptainer_sif_preheat import _ssh_config; sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(); p=Path("configs/preheat_swe_rpg_python3_smoke_20261012.json"); assert sha(p)=="8a97ba6302ec51273525e6990d02291f5d29055cf612be3696c3c676844463ae"; c=json.loads(p.read_text()); assert sha(c["source_request_manifest"])==c["source_request_manifest_sha256"]; assert sha(c["registry_audit"])==c["registry_audit_sha256"]; assert sha("scripts/tools/login_apptainer_sif_preheat.py")==c["downloader_sha256"]; assert len(c["images"])==3; assert _ssh_config(Path("configs/ulhpc_submit.yaml"))[:2]==("twang@access-iris.uni.lu","8022")'
set --
if [[ "$MODE" == "--dry-run" ]]; then set -- --dry-run; fi
HPC_PROJECT_ROOT=/scratch/users/twang/vibe-coding-planning
OPERATION_ROOT="$HPC_PROJECT_ROOT/operations/swe-rpg-python3-smoke-v1-20261012"
exec conda run --no-capture-output -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --images-json configs/preheat_swe_rpg_python3_smoke_20261012.json \
  --ulhpc-config configs/ulhpc_submit.yaml --sif-cache-dir "$HPC_PROJECT_ROOT/shared/sif-cache" \
  --apptainer-cache-dir "$OPERATION_ROOT/apptainer-cache" --apptainer-tmp-dir "$OPERATION_ROOT/apptainer-tmp" \
  --provenance-output "$OPERATION_ROOT/provenance.json" --failed-output "$OPERATION_ROOT/failures.tsv" \
  --lock-file "$HPC_PROJECT_ROOT/shared/sif-cache/.preheat.lock" \
  --missing-only --timeout 3600 --max-attempts 1 --retry-backoff 0 --cleanup-tmp "$@"
