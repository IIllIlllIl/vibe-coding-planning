"""No-model preparation using a frozen SIF; acquisition is a separate login preheat.

Bounded smoke history preparation may run on login. Startup validation must run
on the actual compute environment through the existing submission framework.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from scripts.tools.prepare_swe_chat_hybrid_history import prepare
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot, require_evidence_sif
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--stage", choices=("history", "startup"), required=True)
    args = parser.parse_args()
    if args.stage == "startup" and not os.environ.get("SLURM_JOB_ID"):
        parser.error("startup verification requires the actual compute-node environment")
    args.operation.mkdir(parents=True, exist_ok=False)
    status = {"status": "running", "model_calls": 0}
    report = args.operation / "status.json"
    atomic_json(report, status)
    try:
        config = load_optimization_config(args.config, require_api_keys=False)
        verify_hybrid_snapshot(config)
        sif = require_evidence_sif(config)
        records = prepare(config, check_only=args.stage == "startup")
        atomic_json(args.operation / "history.json", {"records": records,
                    "complete": all(r["status"] == "prepared" for r in records)})
        if not all(r["status"] == "prepared" for r in records):
            raise ValueError("missing history bundles; run bounded history preparation first")
        if args.stage == "history":
            status.update(status="history_prepared", sif_sha256=config.hybrid.evidence_sif_sha256,
                          cases=len(records), startup_verified=False)
            return
        model = config.hybrid.checker_model
        subprocess.run([sys.executable, "scripts/tools/verify_codex_sif_startup.py",
            "--case-sif", str(sif), "--evidence-sif", str(sif),
            "--output-dir", str(args.operation / "startup"),
            "--codex-binary", model["codex_binary"], "--codex-auth-file", model["codex_auth_file"],
            "--model", model["model"], "--reasoning-effort", model["reasoning_effort"],
            "--codex-version", model["codex_version"], "--generic-isolation-image", "--preflight-only"], check=True)
        status.update(status="completed", sif_sha256=config.hybrid.evidence_sif_sha256, cases=len(records))
    except Exception as exc:
        status.update(status="failed", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        atomic_json(report, status)


if __name__ == "__main__":
    main()
