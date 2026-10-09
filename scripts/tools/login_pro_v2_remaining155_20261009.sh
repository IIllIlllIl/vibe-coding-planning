#!/usr/bin/env bash
set -euo pipefail
MODE="${1:---dry-run}"
if [[ "$MODE" != "--dry-run" && "$MODE" != "--run" ]]; then exit 2; fi
conda run -n mini-swe python -c 'import hashlib,json; from pathlib import Path; assert hashlib.sha256(Path("configs/preheat_swe_bench_pro_v2_remaining155_20261009.json").read_bytes()).hexdigest() == "9bc1d04db77ead06556f0c542222202b6cddd55009b9e7da0a52d6fa05ee164a"; d=json.loads(Path("configs/frozen_swe_bench_pro/v2-ansible82-audited-v1-20261009/images.json").read_text()); assert len(d["records"])==82 and all(r["base_commit_verified"] and r["status"]=="audited" for r in d["records"].values())'
conda run -n mini-swe python -c 'import hashlib,json; from pathlib import Path; a=json.loads(Path("configs/frozen_swe_bench_pro/v2-ansible82-audited-v1-20261009/acceptance.json").read_text()); assert hashlib.sha256(Path(a["image_manifest"]).read_bytes()).hexdigest()==a["image_manifest_sha256"]; assert hashlib.sha256(Path(a["retrospective_registry_evidence"]).read_bytes()).hexdigest()==a["retrospective_registry_evidence_sha256"]'
set --
if [[ "$MODE" == "--dry-run" ]]; then set -- --dry-run; fi
ROOT=/scratch/users/twang/vibe-coding-planning
OP="$ROOT/operations/swe-bench-pro-v2-remaining155-preheat-v1-20261009"
exec conda run --no-capture-output -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --images-json configs/preheat_swe_bench_pro_v2_remaining155_20261009.json \
  --ulhpc-config configs/ulhpc_submit.yaml --sif-cache-dir "$ROOT/shared/sif-cache" \
  --apptainer-cache-dir "$ROOT/operations/swe-bench-pro-v2-ansible82-preheat-v1-20261008/apptainer-cache" \
  --apptainer-tmp-dir "$OP/apptainer-tmp" --provenance-output "$OP/provenance.json" \
  --failed-output "$OP/failures.tsv" --lock-file "$ROOT/shared/sif-cache/.preheat.lock" \
  --missing-only --timeout 3600 --max-attempts 1 --retry-backoff 0 --cleanup-tmp "$@"
