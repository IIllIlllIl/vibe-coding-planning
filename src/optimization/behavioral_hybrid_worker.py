"""Independent hybrid Reflector/Curator worker; transport owned by SlurmTaskBatch."""
import argparse
from pathlib import Path
import json
from datetime import datetime, timezone

from src.optimization.audit import redact_sensitive
from src.optimization.behavioral_hybrid_runtime import run_local_phase, phase_identity
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json


def run_task(*, config_path, manifest_path, output_path, attempt_dir):
    manifest = {}
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        manifest = json.loads(manifest_path.read_text())
        if manifest["mode"] != "behavioral_hybrid_phase":
            raise ValueError("invalid hybrid worker mode")
        config = load_optimization_config(config_path)
        if manifest["role"] not in {"reflector", "curator", "refiner"} or not config.hybrid.enabled:
            raise ValueError("unsupported hybrid role or disabled hybrid configuration")
        payload = manifest["payload"]
        if manifest["fingerprint"] != phase_identity(config, manifest["role"], payload):
            raise ValueError("hybrid worker config/source/input fingerprint mismatch")
        records = [payload["record"]] if manifest["role"] == "reflector" else payload.get("records", [])
        if manifest["role"] == "refiner":
            if not config.hybrid.refiner_enabled or payload.get("split") != "train":
                raise ValueError("Refiner requires explicitly training-only input")
            records = [{"split": "train"}]
        if not records or any(record.get("split") != "train" for record in records):
            raise ValueError("hybrid workers accept only training evidence")
        feedback = ""
        if output_path.exists():
            previous = json.loads(output_path.read_text())
            feedback = str(previous.get("error", ""))
        output, trajectory = run_local_phase(config, manifest["role"], manifest["payload"], attempt_dir,
                                              retry_feedback=feedback)
        atomic_json(output_path, redact_sensitive(dict(status="completed", fingerprint=manifest["fingerprint"],
                    role=manifest["role"], started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(), agent_output=output, trajectory=trajectory)))
        return 0
    except Exception as exc:
        value = redact_sensitive(dict(status="agent_failed", fingerprint=manifest.get("fingerprint"),
                     role=manifest.get("role"), started_at=started_at,
                     finished_at=datetime.now(timezone.utc).isoformat(),
                     error_type=type(exc).__name__, error=str(exc), failure_kind="operational"))
        atomic_json(attempt_dir / "failure.json", value)
        atomic_json(output_path, value)
        return 1


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for name in ("config", "manifest", "output", "attempt-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    return run_task(config_path=args.config, manifest_path=args.manifest,
                    output_path=args.output, attempt_dir=args.attempt_dir)


if __name__ == "__main__":
    raise SystemExit(main())
