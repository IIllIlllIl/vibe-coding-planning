#!/usr/bin/env bash
# Prepared remaining75 SIF acquisition; default mode performs read-only planning.
set -euo pipefail
MODE="${1:---dry-run}"
if [[ "$MODE" != "--dry-run" && "$MODE" != "--run" ]]; then
  echo "Usage: $0 [--dry-run|--run]" >&2
  exit 2
fi
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
conda run -n mini-swe python -c 'import hashlib,json; from pathlib import Path; from scripts.tools.login_apptainer_sif_preheat import _ssh_config; sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(); p=Path("configs/preheat_swe_rpg_python75_remaining_20261012.json"); assert sha(p)=="4c76f300845f7e0404585cf014843369b3d4a38e89e47f913412a204b8b482a9"; c=json.loads(p.read_text()); assert sha(c["source_request_manifest"])==c["source_request_manifest_sha256"]; assert sha(c["registry_audit"])==c["registry_audit_sha256"]; assert sha("scripts/tools/login_apptainer_sif_preheat.py")==c["downloader_sha256"]; assert len(c["images"])==75; assert sha(c["accepted_smoke_report"])==c["accepted_smoke_report_sha256"]; a=json.loads(Path(c["accepted_smoke_report"]).read_text()); assert a["passed"] and len(a["records"])==3; full=json.loads(Path(c["source_request_manifest"]).read_text()); assert c["requests"]==[r for r in full["requests"] if r["image_ref"] not in a["records"]]; assert _ssh_config(Path("configs/ulhpc_submit.yaml"))[:2]==("twang@access-iris.uni.lu","8022")'
set --
if [[ "$MODE" == "--dry-run" ]]; then set -- --dry-run; fi
HPC_PROJECT_ROOT=/scratch/users/twang/vibe-coding-planning
OPERATION_ROOT="$HPC_PROJECT_ROOT/operations/swe-rpg-python75-remaining-v1-20261012"
SHARED_OCI_CACHE="$HPC_PROJECT_ROOT/operations/swe-rpg-python3-smoke-v1-20261012/apptainer-cache"
exec conda run --no-capture-output -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --images-json configs/preheat_swe_rpg_python75_remaining_20261012.json \
  --ulhpc-config configs/ulhpc_submit.yaml --sif-cache-dir "$HPC_PROJECT_ROOT/shared/sif-cache" \
  --apptainer-cache-dir "$SHARED_OCI_CACHE" --apptainer-tmp-dir "$OPERATION_ROOT/apptainer-tmp" \
  --provenance-output "$OPERATION_ROOT/provenance.json" --failed-output "$OPERATION_ROOT/failures.tsv" \
  --lock-file "$HPC_PROJECT_ROOT/shared/sif-cache/.preheat.lock" \
  --missing-only --timeout 3600 --max-attempts 1 --retry-backoff 0 --cleanup-tmp "$@"
