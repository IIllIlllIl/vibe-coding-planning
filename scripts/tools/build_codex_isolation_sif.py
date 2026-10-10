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
import shutil
import tarfile
import tempfile

from src.environment.apptainer_env import _image_to_sif_name
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook_hpc_executor import _file_sha256


def build(*, definition=None, cache, image, operation, base_sif=None,
          base_sha256=None, rg_archive=None, rg_sha256=None):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("SIF build must run in an authorized compute-node job")
    if base_sif is not None:
        base_sif = base_sif.resolve(strict=True)
        rg_archive = rg_archive.resolve(strict=True)
        if _file_sha256(base_sif) != base_sha256 or _file_sha256(rg_archive) != rg_sha256:
            raise ValueError("Frozen base SIF or rg archive hash mismatch")
    else:
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
            if base_sif is None:
                subprocess.run(["apptainer", "build", "--fakeroot", str(temporary), str(definition)],
                               env=env, check=True)
            else:
                # No apt, UID transitions, subuid mappings or privileged build.
                sandbox = Path(tmp) / "sandbox"
                subprocess.run(["apptainer", "build", "--sandbox", str(sandbox), str(base_sif)],
                               env=env, check=True)
                with tarfile.open(rg_archive) as archive:
                    members = [m for m in archive.getmembers()
                               if m.isfile() and m.name.endswith("/rg")]
                    if len(members) != 1:
                        raise ValueError("Archive must contain exactly one regular rg binary")
                    destination = sandbox / "usr/local/bin/rg"
                    if destination.is_symlink() or destination.exists():
                        raise ValueError("Refuse to replace an existing rg binary")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(members[0]) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    destination.chmod(0o755)
                env["APPTAINER_MKSQUASHFS_PROCS"] = "1"
                subprocess.run(["apptainer", "build", str(temporary), str(sandbox)],
                               env=env, check=True)
            subprocess.run(["apptainer", "inspect", "--json", str(temporary)], check=True)
            tool_checks = {}
            for tool in ("python3", "git", "rg"):
                check = subprocess.run(["apptainer", "exec", "--containall", "--no-home",
                                        "--pwd", "/", str(temporary), tool, "--version"],
                                       env=env, check=True, capture_output=True, text=True)
                tool_checks[tool] = check.stdout.strip()
            digest = _file_sha256(temporary)
            temporary.replace(target)
        report = {"image": image, "sif_path": str(target), "sif_sha256": digest,
                  "definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest() if definition else None,
                  "base_sif_sha256": base_sha256, "rg_archive_sha256": rg_sha256,
                  "tool_checks": tool_checks,
                  "job_id": os.environ["SLURM_JOB_ID"], "preflight_passed": False}
        atomic_json(operation / "sif-build.json", report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--definition", type=Path)
    mode.add_argument("--base-sif", type=Path)
    parser.add_argument("--base-sha256")
    parser.add_argument("--rg-archive", type=Path)
    parser.add_argument("--rg-sha256")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--operation", type=Path, required=True)
    args = parser.parse_args()
    if args.base_sif and not all((args.base_sha256, args.rg_archive, args.rg_sha256)):
        parser.error("--base-sif requires --base-sha256, --rg-archive and --rg-sha256")
    build(definition=args.definition, cache=args.cache, image=args.image, operation=args.operation,
          base_sif=args.base_sif, base_sha256=args.base_sha256,
          rg_archive=args.rg_archive, rg_sha256=args.rg_sha256)


if __name__ == "__main__":
    main()
