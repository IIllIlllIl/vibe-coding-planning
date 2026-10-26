"""Serial permission-only preparation using the established SIF builder.

No model, workflow controller or scheduler is implemented here. Each input is
immutable; a failed image is recorded and does not prevent independent builds.
"""
import argparse
import json
import os
from pathlib import Path

from scripts.tools.build_codex_isolation_sif import build
from src.optimization.hpc.task_batch import atomic_json
from src.swe_verified_pce.dataset import file_sha256


def run(plan_path, *, max_cases=0, work_root=None):
    plan = json.loads(plan_path.read_text())
    operation = Path(plan["operation"])
    operation.mkdir(parents=True, exist_ok=True)
    records = []
    for item in plan["records"][:max_cases or None]:
        directory = operation / item["instance_id"]
        saved = directory / "sif-build.json"
        attempt = directory / 'attempts' / ('job-' + os.environ.get('SLURM_JOB_ID', 'local'))
        try:
            if saved.is_file():
                result = json.loads(saved.read_text())
                if (result.get("base_sif_sha256") != item["base_sha256"]
                        or result.get("image") != item["derived_ref"]
                        or result.get("permission_audit", {}).get("allowlisted_paths") != item["permission_paths"]
                        or not result.get("permission_audit", {}).get("passed")
                        or file_sha256(Path(result["sif_path"])) != result["sif_sha256"]):
                    raise ValueError("previous derived image failed identity/integrity validation")
                status = "reused"
            else:
                result = build(base_sif=Path(item["base_sif"]), base_sha256=item["base_sha256"],
                    permission_paths=item["permission_paths"], cache=Path(plan["cache"]),
                    image=item["derived_ref"], operation=attempt, work_root=work_root)
                atomic_json(saved, result)
                status = "built"
            records.append(dict(instance_id=item["instance_id"], status=status, report=result,
                                report_path=str(saved), report_sha256=file_sha256(saved)))
        except Exception as exc:
            failure = dict(instance_id=item["instance_id"], status="failed", error_type=type(exc).__name__, error=str(exc))
            atomic_json(attempt / "build-failure.json", failure)
            records.append(failure)
        atomic_json(operation / "repair-summary.json", dict(schema_version=1,
            plan_sha256=file_sha256(plan_path), records=records, model_calls=0))
        print(json.dumps({k: v for k, v in records[-1].items() if k != "report"}), flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--work-root", type=Path,
                        help="Existing build temporary root; audit reports remain in the operation directory")
    args = parser.parse_args()
    if args.max_cases < 0:
        parser.error("--max-cases must be nonnegative")
    return 0 if all(r["status"] != "failed" for r in run(
        args.plan, max_cases=args.max_cases, work_root=args.work_root)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
