"""Independent hybrid Reflector/Curator worker; transport owned by SlurmTaskBatch."""
from pathlib import Path
import json
from datetime import datetime, timezone

from src.optimization.audit import redact_sensitive, redact_agent_record
from src.optimization.behavioral_hybrid_runtime import run_local_phase, phase_identity
from src.optimization.config import load_optimization_config
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.hpc.worker_contract import worker_arguments, retry_feedback, worker_envelope, worker_failure
from src.optimization.playbook_runtime import PlaybookAgentOutputContractError


def run_task(*, config_path, manifest_path, output_path, attempt_dir, previous_output_path=None):
    manifest = {}
    started_at = datetime.now(timezone.utc).isoformat()
    attempt_dir.mkdir(parents=True, exist_ok=True)
    stage = "input_load"
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
        feedback = retry_feedback(previous_output_path)
        stage = "agent_execution"
        output, trajectory = run_local_phase(config, manifest["role"], manifest["payload"], attempt_dir,
                                              retry_feedback=feedback)
        stage = "output_write"
        atomic_json(output_path, redact_agent_record(worker_envelope(
            manifest, started_at, "completed", agent_output=output, trajectory=trajectory)))
        return 0
    except Exception as exc:
        stage = getattr(exc, "worker_failure_stage", stage)
        value = redact_sensitive(worker_failure(manifest, started_at, stage, exc,
            contract_error=stage == "agent_output_validation" or isinstance(exc, PlaybookAgentOutputContractError)))
        if stage == "agent_output_validation":
            completion = attempt_dir / "completion.json"
            if completion.is_file():
                value["raw_agent_output"] = json.loads(completion.read_text())["agent_output"]
            if hasattr(exc, "violations"):
                value["validation_violations"] = exc.violations
            value = redact_sensitive(value)
        atomic_json(attempt_dir / "failure.json", value)
        atomic_json(output_path, value)
        return 1


def main():
    args = worker_arguments()
    return run_task(config_path=args.config, manifest_path=args.manifest,
                    output_path=args.output, attempt_dir=args.attempt_dir,
                    previous_output_path=args.previous_output)


if __name__ == "__main__":
    raise SystemExit(main())
