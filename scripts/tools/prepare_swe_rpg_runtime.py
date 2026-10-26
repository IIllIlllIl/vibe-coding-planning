"""No-model RPG preparation adapter over existing phase waves and diagnostics.

Login runs only the shared manifest/submission coordinator. Compute elements
reuse the existing history preheater and Base/Gold evaluator, without new
retry, scheduling, checkpoint or scientific scoring logic.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from src.exceptions import ControllerYield
from src.optimization.hpc.phase_wave import prepare_phase_wave, build_phase_array_script
from src.optimization.hpc.task_batch import SlurmTaskBatch, atomic_json
from src.optimization.hpc.worker_contract import worker_envelope
from src.swe_rpg_pce.config import load_swe_rpg_pce_config
from src.swe_rpg_pce.dataset import load_swe_rpg_pce_cases
from src.swe_verified_pce.dataset import file_sha256


def identity(config):
    root = Path(__file__).resolve().parents[2]
    paths = [config.config_path, config.dataset_snapshot / "manifest.json", config.image_manifest,
             config.selection_manifest, root / "scripts/tools/prepare_swe_rpg_runtime.py",
             root / "scripts/tools/preheat_polybench_repository_history.py",
             root / "scripts/tools/verify_swe_rpg_evaluator.py",
             root / "src/environment/repository_history.py", root / "src/swe_rpg_pce/evaluator.py",
             root / "src/swe_rpg_pce/eval_driver.py", root / "src/optimization/hpc/phase_wave.py",
             root / "src/optimization/hpc/task_batch.py"]
    return hashlib.sha256(json.dumps([file_sha256(p) for p in paths]).encode()).hexdigest()


def validate(task, value, fingerprint):
    if (value.get("fingerprint") != fingerprint or value.get("instance_id") != task.instance_id
            or value.get("status") != "completed" or set(value.get("checks", {})) != {"history", "calibration"}):
        raise ValueError("RPG preparation output identity/schema mismatch")


def coordinate(config_path, *, submit=False):
    config = load_swe_rpg_pce_config(config_path, require_api_keys=False)
    cases = load_swe_rpg_pce_cases(config.dataset_snapshot, config.image_manifest)[0]
    if not set(config.instance_ids) <= {case.instance_id for case in cases}:
        raise ValueError("preparation selection is outside verified source/image records")
    fingerprint = identity(config)
    batch = config.run_dir / "hpc_tasks" / "preparation" / fingerprint
    manifests = [dict(role="rpg_runtime_preparation", fingerprint=fingerprint,
                      instance_id=i, task_index=n) for n, i in enumerate(config.instance_ids)]
    tasks = prepare_phase_wave(batch_dir=batch, role="rpg_runtime_preparation", fingerprint=fingerprint,
                              manifests=manifests, instance_ids=config.instance_ids)
    def script(indices, attempt):
        path = batch / f"attempt_{attempt:02d}.sbatch"
        text = build_phase_array_script(hpc=config.hpc, batch_dir=batch, indices=indices,
            attempt=attempt, job_name=f"rpg-prep-{fingerprint[:12]}-a{attempt}",
            config_path=config.config_path, worker_module="scripts.tools.prepare_swe_rpg_runtime",
            modules=(config.hpc.python_module, config.hpc.container_module),
            source_env=False, credential_env=None)
        # A login coordinator has no exported compute-node module function.
        # Match the successful ulhpc-submit diagnostic's login-shell boundary.
        path.write_text(text.replace("#!/usr/bin/env bash\n", "#!/bin/bash -l\n", 1))
        return path
    if not submit:
        return {"status": "prepared", "batch_dir": str(batch), "tasks": len(tasks),
                "script": str(script(list(range(len(tasks))), 1)), "model_calls": 0}
    try:
        outputs = SlurmTaskBatch(replace(config.hpc, submit=True)).run(batch_dir=batch, fingerprint=fingerprint, tasks=tasks,
            job_name=lambda attempt: f"rpg-prep-{fingerprint[:12]}-a{attempt}", write_script=script,
            validate_output=lambda task, value: validate(task, value, fingerprint))
    except ControllerYield as exc:
        return {"status": "waiting", "batch_dir": exc.batch_dir, "job_id": exc.job_id, "model_calls": 0}
    summary = {"status": "completed", "model_calls": 0, "records": outputs,
        "all_checks_passed": all(all(c["passed"] for c in row["checks"].values()) for row in outputs)}
    atomic_json(config.run_dir / "preparation_summary.json", summary)
    return summary


def worker(config_path, manifest_path, output_path, attempt_dir):
    config = load_swe_rpg_pce_config(config_path, require_api_keys=False)
    manifest = json.loads(manifest_path.read_text())
    if manifest["fingerprint"] != identity(config) or manifest["instance_id"] not in config.instance_ids:
        raise ValueError("RPG preparation task/source identity mismatch")
    attempt_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    checks = {}
    for kind in ("history", "calibration"):
        directory = attempt_dir / kind
        if kind == "history":
            command = [sys.executable, "-m", "scripts.tools.preheat_polybench_repository_history",
                "--config", str(config_path), "--instance-id", manifest["instance_id"], "--output-dir", str(directory)]
            report_path = directory / "history_preheat_summary.json"
        else:
            command = [sys.executable, "-m", "scripts.tools.verify_swe_rpg_evaluator",
                "--config", str(config_path), "--instance-id", manifest["instance_id"], "--output-dir", str(directory)]
            report_path = directory / "report.json"
        with (attempt_dir / f"{kind}.stdout").open("w") as stdout, (attempt_dir / f"{kind}.stderr").open("w") as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
        report = json.loads(report_path.read_text()) if report_path.is_file() else None
        checks[kind] = {"returncode": completed.returncode, "report_path": str(report_path),
            "report_sha256": file_sha256(report_path) if report is not None else None,
            "passed": completed.returncode == 0 and report is not None and
                      (report.get("passed") is True if kind == "calibration" else
                       len(report.get("records", [])) == 1 and report["records"][0]["status"] in {"cached", "prepared"})}
    value = worker_envelope(manifest, started, "completed", checks=checks, model_calls=0)
    atomic_json(output_path, value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--submit", action="store_true")
    for name in ("manifest", "output", "attempt-dir", "previous-output"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.manifest:
        result = worker(args.config, args.manifest, args.output, args.attempt_dir)
    else:
        result = coordinate(args.config, submit=args.submit)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
