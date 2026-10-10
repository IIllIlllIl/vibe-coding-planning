"""Build the generic inspection SIF on an explicitly authorized compute job.

This is definition-build glue, not another HPC submission or download service.
It shares the preheater writer lock and refuses to overwrite an existing image.
"""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from src.environment.apptainer_env import _image_to_sif_name
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook_hpc_executor import _file_sha256


def build(*, definition, cache, image, operation):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("SIF build must run in an authorized compute-node job")
    definition = definition.resolve(strict=True)
    cache.mkdir(parents=True, exist_ok=True)
    operation.mkdir(parents=True, exist_ok=True)
    target = cache / _image_to_sif_name(image)
    with (cache / ".preheat.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if target.exists():
            raise FileExistsError("Existing SIF is retained; verify it separately, never overwrite it")
        with tempfile.TemporaryDirectory(prefix="codex-sif-build-", dir=operation) as tmp:
            temporary = Path(tmp) / "image.sif"
            env = dict(os.environ)
            env["APPTAINER_CACHEDIR"] = str(operation / "apptainer-cache")
            env["APPTAINER_TMPDIR"] = str(Path(tmp) / "apptainer-tmp")
            Path(env["APPTAINER_TMPDIR"]).mkdir()
            subprocess.run(["apptainer", "build", "--fakeroot", str(temporary), str(definition)],
                           env=env, check=True)
            subprocess.run(["apptainer", "inspect", "--json", str(temporary)], check=True)
            digest = _file_sha256(temporary)
            temporary.replace(target)
        report = {"image": image, "sif_path": str(target), "sif_sha256": digest,
                  "definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest(),
                  "job_id": os.environ["SLURM_JOB_ID"], "preflight_passed": False}
        atomic_json(operation / "sif-build.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--operation", type=Path, required=True)
    args = parser.parse_args()
    build(definition=args.definition, cache=args.cache, image=args.image, operation=args.operation)


if __name__ == "__main__":
    main()
