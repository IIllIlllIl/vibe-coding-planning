#!/usr/bin/env bash
# Prepared single-image RPG retry; default mode performs read-only planning.
set -euo pipefail
MODE="${1:---dry-run}"
if [[ "$MODE" != "--dry-run" && "$MODE" != "--run" ]]; then
  echo "Usage: $0 [--dry-run|--run]" >&2
  exit 2
fi
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
conda run -n mini-swe python -c 'import hashlib,json; from pathlib import Path; from scripts.tools.login_apptainer_sif_preheat import _ssh_config; sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(); p=Path("configs/preheat_swe_rpg_pdm2795_retry_20261014.json"); assert sha(p)=="a697c5b1f9e1556f79cd60f258f61c35c9327f62ca0cdcb03deb84fbeba7686f"; c=json.loads(p.read_text()); assert sha(c["source_request_manifest"])==c["source_request_manifest_sha256"]; assert sha(c["registry_audit"])==c["registry_audit_sha256"]; assert sha("scripts/tools/login_apptainer_sif_preheat.py")==c["downloader_sha256"]; assert len(c["images"])==1; assert sha(c["original_config"])==c["original_config_sha256"]; assert sha(c["original_provenance_snapshot"])==c["original_provenance_sha256"]; old=json.loads(Path(c["original_provenance_snapshot"]).read_text()); failed={i for i,r in old["records"].items() if r["status"]=="failed"}; assert failed==set(c["images"]); full=json.loads(Path(c["source_request_manifest"]).read_text()); assert c["requests"]==[r for r in full["requests"] if r["image_ref"] in failed]; assert _ssh_config(Path("configs/ulhpc_submit.yaml"))[:2]==("twang@access-iris.uni.lu","8022")'
set --
if [[ "$MODE" == "--dry-run" ]]; then set -- --dry-run; fi
HPC_PROJECT_ROOT=/scratch/users/twang/vibe-coding-planning
OPERATION_ROOT="$HPC_PROJECT_ROOT/operations/swe-rpg-pdm2795-retry-v1-20261014"
SHARED_OCI_CACHE="$HPC_PROJECT_ROOT/operations/swe-rpg-python3-smoke-v1-20261012/apptainer-cache"
exec conda run --no-capture-output -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --images-json configs/preheat_swe_rpg_pdm2795_retry_20261014.json \
  --ulhpc-config configs/ulhpc_submit.yaml --sif-cache-dir "$HPC_PROJECT_ROOT/shared/sif-cache" \
  --apptainer-cache-dir "$SHARED_OCI_CACHE" --apptainer-tmp-dir "$OPERATION_ROOT/apptainer-tmp" \
  --provenance-output "$OPERATION_ROOT/provenance.json" --failed-output "$OPERATION_ROOT/failures.tsv" \
  --lock-file "$HPC_PROJECT_ROOT/shared/sif-cache/.preheat.lock" \
  --missing-only --timeout 7200 --max-attempts 1 --retry-backoff 0 --cleanup-tmp "$@"
