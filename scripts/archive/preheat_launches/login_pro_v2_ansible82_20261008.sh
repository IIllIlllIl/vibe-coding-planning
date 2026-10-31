#!/usr/bin/env bash
set -euo pipefail
MODE="${1:---dry-run}"
if [[ "$MODE" != "--dry-run" && "$MODE" != "--run" ]]; then exit 2; fi
conda run -n mini-swe python -c 'import hashlib; from pathlib import Path; assert hashlib.sha256(Path("configs/preheat_swe_bench_pro_v2_ansible82_20261008.json").read_bytes()).hexdigest() == "356670ac4a88bc0d0548cf1138b5a8af5588a26216c96a348866e8bdcc7d96dc"'
set --
if [[ "$MODE" == "--dry-run" ]]; then set -- --dry-run; fi
ROOT=/scratch/users/twang/vibe-coding-planning
OP="$ROOT/operations/swe-bench-pro-v2-ansible82-preheat-v1-20261008"
exec conda run --no-capture-output -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --images-json configs/preheat_swe_bench_pro_v2_ansible82_20261008.json \
  --ulhpc-config configs/ulhpc_submit.yaml \
  --sif-cache-dir "$ROOT/shared/sif-cache" \
  --apptainer-cache-dir "$OP/apptainer-cache" --apptainer-tmp-dir "$OP/apptainer-tmp" \
  --provenance-output "$OP/provenance.json" --failed-output "$OP/failures.tsv" \
  --lock-file "$ROOT/shared/sif-cache/.preheat.lock" \
  --missing-only --timeout 3600 --max-attempts 1 --retry-backoff 0 --cleanup-tmp "$@"
