"""One-case no-Agent Base/Gold calibration through the real RPG evaluator.

Run only with explicit compute authorization via the existing HPC submit tools.
This is a diagnostic entry, not a scheduler, dataset cleaner or PCE controller.
"""
import argparse
import json
from pathlib import Path
import tempfile

from src.environment.docker_env import configure_docker_capacity
from src.optimization.hpc.task_batch import atomic_json
from src.swe_rpg_pce.config import load_swe_rpg_pce_config
from src.swe_rpg_pce.dataset import load_swe_rpg_pce_cases
from src.swe_rpg_pce.runner import SWERPGPCERunner


def verify(*, config_path, instance_id, output_dir):
    config = load_swe_rpg_pce_config(config_path, require_api_keys=False)
    cases = {case.instance_id: case for case in load_swe_rpg_pce_cases(config.dataset_snapshot, config.image_manifest)[0]}
    if instance_id not in cases or (config.instance_ids and instance_id not in config.instance_ids):
        raise ValueError("calibration task is outside the frozen selection")
    output_dir = Path(output_dir)
    if (output_dir / "report.json").exists():
        raise ValueError("calibration output already exists; use a fresh diagnostic identity")
    output_dir.mkdir(parents=True, exist_ok=True)
    case = cases[instance_id]
    capacity = configure_docker_capacity(config.docker, max_concurrent=1, enable_docker_maintenance=False)
    runner = SWERPGPCERunner(config, capacity, checkpoint_dir=output_dir / "unused_checkpoints",
        checkpoint_identity="evaluator_calibration_only", attempt_dir=output_dir / "verification")
    runner._verify_sif(case)
    results = {}
    for name, patch in (("base", None), ("gold", case.gold_patch)):
        evidence = output_dir / name
        evidence.mkdir()
        try:
            with tempfile.TemporaryDirectory(prefix="workspace-", dir=evidence) as temporary:
                result = runner.evaluator(patch, case, container=config.container, capacity_window=capacity,
                    workdir=case.working_directory, phase_workdir=Path(temporary) / "repository",
                    repository_baseline_dir=evidence / "evidence", command_timeout=config.plan.timeout,
                    result_callback=lambda value, path=evidence / "result.json": atomic_json(path, value))
            results[name] = result
        except Exception as exc:
            results[name] = {"status": "operationally_incomplete", "error_type": type(exc).__name__,
                "error": str(exc), "evidence": getattr(exc, "evidence", {})}
            atomic_json(evidence / "failure.json", results[name])
    base, gold = results["base"], results["gold"]
    f2p = base.get("rpg_report", {}).get("FAIL_TO_PASS", {})
    nonpassing = any(f2p.get(key) for key in ("failure", "skipped", "missing"))
    report = {"schema_version": 1, "instance_id": instance_id, "model_calls": 0,
        "purpose": "official_rpg_base_gold_calibration_only", "working_directory": case.working_directory,
        "sif_sha256": case.image.sif_sha256,
        "passed": base.get("evaluator_resolved") is False and gold.get("evaluator_resolved") is True and nonpassing,
        "base_explicit_f2p_failure": bool(f2p.get("failure")), "results": results}
    atomic_json(output_dir / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = verify(config_path=args.config, instance_id=args.instance_id, output_dir=args.output_dir)
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
