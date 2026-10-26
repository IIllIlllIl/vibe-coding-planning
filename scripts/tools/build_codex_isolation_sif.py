"""Build the generic inspection SIF on an explicitly authorized compute job.

This is definition-build glue, not another HPC submission or download service.
It shares the preheater writer lock and refuses to overwrite an existing image.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import time

from src.environment.apptainer_env import _image_to_sif_name
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.playbook_hpc_executor import _file_sha256


@contextmanager
def _timed_stage(operation, timings, name):
    started = time.monotonic()
    status = "failed"
    print(json.dumps(dict(stage=name, event="started")), flush=True)
    try:
        yield
        status = "passed"
    finally:
        timings[name] = dict(seconds=round(time.monotonic() - started, 3), status=status)
        atomic_json(operation / "build-timings.json", timings)
        print(json.dumps(dict(stage=name, **timings[name])), flush=True)


def _publish_verified(source, target, digest):
    """Copy across filesystems, then verify and rename only within the cache."""
    with tempfile.NamedTemporaryFile(prefix=".sif-publish-", suffix=".partial",
                                     dir=target.parent, delete=False) as output:
        pending = Path(output.name)
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, output)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            pending.unlink(missing_ok=True)
            raise
    try:
        shutil.copymode(source, pending)
        if _file_sha256(pending) != digest:
            raise ValueError("SIF publication copy hash mismatch")
        if target.exists():
            raise FileExistsError("Existing SIF is retained; never overwrite it")
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def build(*, definition=None, cache, image, operation, base_sif=None,
          base_sha256=None, rg_archive=None, rg_sha256=None, permission_paths=None,
          work_root=None):
    started = time.monotonic()
    timings = {}
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("SIF build must run in an authorized compute-node job")
    if work_root is not None:
        if not permission_paths:
            raise ValueError("work_root is supported only for permission-only repair")
        work_root = Path(work_root).resolve(strict=True)
        if not work_root.is_dir():
            raise ValueError("work_root must be an existing directory")
    if base_sif is not None:
        base_sif = base_sif.resolve(strict=True)
        if permission_paths:
            from src.environment.sif_permissions import validate_roots
            validate_roots(permission_paths)
            if rg_archive is not None:
                raise ValueError("permission-only repair cannot also add rg")
        else:
            rg_archive = rg_archive.resolve(strict=True)
        if _file_sha256(base_sif) != base_sha256 or (rg_archive is not None and _file_sha256(rg_archive) != rg_sha256):
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
        with tempfile.TemporaryDirectory(prefix="codex-sif-build-", dir=work_root or operation) as tmp:
            temporary = Path(tmp) / "image.sif"
            env = dict(os.environ)
            env["APPTAINER_CACHEDIR"] = str(operation / "apptainer-cache")
            env["APPTAINER_TMPDIR"] = str(Path(tmp) / "apptainer-tmp")
            Path(env["APPTAINER_TMPDIR"]).mkdir()
            env["APPTAINER_MKSQUASHFS_PROCS"] = "1"
            permission_audit = None
            if base_sif is None:
                subprocess.run(["apptainer", "build", "--fakeroot", str(temporary), str(definition)],
                               env=env, check=True)
            elif permission_paths:
                from src.environment.sif_permissions import (
                    image_metadata, prepare_repair, content_inventory, verify_repair, unpack, repack_image,
                )
                parent = base_sif
                if work_root is not None:
                    with _timed_stage(operation, timings, "copy_parent_to_work_disk"):
                        parent = Path(tmp) / "parent.sif"
                        shutil.copyfile(base_sif, parent)
                        if _file_sha256(parent) != base_sha256:
                            raise ValueError("Node-local parent SIF copy hash mismatch")
                partition_directory = Path(tmp) if work_root is not None else None
                partition = (partition_directory or operation / "original") / "filesystem.squashfs"
                original = operation / "original"
                original.mkdir(exist_ok=True)
                with _timed_stage(operation, timings, "original_metadata"):
                    metadata = image_metadata(parent, original, env, retain_partition=True,
                                              partition_directory=partition_directory)
                # Release the first extracted tree before the roundtrip extraction:
                # only one large repository/environment tree occupies inode quota.
                with tempfile.TemporaryDirectory(prefix="source-tree-", dir=tmp) as tree:
                    sandbox = Path(tree) / "sandbox"
                    with _timed_stage(operation, timings, "original_unpack"):
                        unpack(partition, sandbox, env)
                        partition.unlink()
                    with _timed_stage(operation, timings, "inventory_and_permissions"):
                        before, desired, changes, options = prepare_repair(sandbox, metadata, permission_paths, operation)
                    with _timed_stage(operation, timings, "pack_sif"):
                        objects = repack_image(parent, temporary, sandbox, options, Path(tmp), env)
                derived = operation / "derived"
                derived.mkdir(exist_ok=True)
                partition = (partition_directory or derived) / "filesystem.squashfs"
                with _timed_stage(operation, timings, "derived_metadata"):
                    new_metadata = image_metadata(temporary, derived, env, retain_partition=True,
                                                  partition_directory=partition_directory)
                with tempfile.TemporaryDirectory(prefix="roundtrip-tree-", dir=tmp) as tree:
                    sandbox = Path(tree) / "sandbox"
                    with _timed_stage(operation, timings, "derived_unpack"):
                        unpack(partition, sandbox, env)
                        partition.unlink()
                    with _timed_stage(operation, timings, "derived_inventory"):
                        after = content_inventory(sandbox)
                with _timed_stage(operation, timings, "verify_permission_audit"):
                    permission_audit = verify_repair(before, after, metadata, new_metadata, desired)
                permission_audit.update(allowlisted_paths=list(permission_paths), changes=changes,
                                        source_sif_sha256=base_sha256, extended_attributes=0,
                                        preserved_sif_objects=objects)
                atomic_json(operation / "permission-audit.json", permission_audit)
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
            for tool in (("python3", "git") if permission_paths else ("python3", "git", "rg")):
                check = subprocess.run(["apptainer", "exec", "--containall", "--no-home",
                                        "--pwd", "/", str(temporary), tool, "--version"],
                                       env=env, check=True, capture_output=True, text=True)
                tool_checks[tool] = check.stdout.strip()
            digest = _file_sha256(temporary)
            if work_root is None:
                temporary.replace(target)
            else:
                with _timed_stage(operation, timings, "publish_verified_sif"):
                    _publish_verified(temporary, target, digest)
        report = {"image": image, "sif_path": str(target), "sif_sha256": digest,
                  "sif_bytes": target.stat().st_size,
                  "definition_sha256": hashlib.sha256(definition.read_bytes()).hexdigest() if definition else None,
                  "base_sif_sha256": base_sha256, "rg_archive_sha256": rg_sha256,
                  "tool_checks": tool_checks,
                  "job_id": os.environ["SLURM_JOB_ID"], "preflight_passed": False}
        if permission_paths:
            timings["total"] = dict(seconds=round(time.monotonic() - started, 3), status="passed")
            atomic_json(operation / "build-timings.json", timings)
            report["build_timings"] = timings
            report["work_storage"] = dict(root=str(work_root or operation),
                                           free_bytes_at_finish=shutil.disk_usage(work_root or operation).free)
            report["permission_audit"] = permission_audit
            report['permission_audit_directory'] = str(operation)
            from src.environment import sif_permissions
            report['build_sources_sha256'] = {
                'builder': _file_sha256(Path(__file__)),
                'permission_helper': _file_sha256(Path(sif_permissions.__file__)),
            }
            report['squashfs_tools_sha256'] = {tool: _file_sha256(Path(shutil.which(tool, path=env['PATH'])))
                                             for tool in ('mksquashfs', 'unsquashfs')}
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
    parser.add_argument("--permission-path", action="append", default=[])
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--operation", type=Path, required=True)
    parser.add_argument("--work-root", type=Path,
                        help="Permission repair only: existing private-build temporary storage root")
    args = parser.parse_args()
    if args.base_sif and (not args.base_sha256 or (not args.permission_path and not all((args.rg_archive, args.rg_sha256)))):
        parser.error("--base-sif requires its hash and either permission paths or rg archive/hash")
    if args.permission_path and not args.base_sif:
        parser.error("permission repair requires --base-sif")
    build(definition=args.definition, cache=args.cache, image=args.image, operation=args.operation,
          base_sif=args.base_sif, base_sha256=args.base_sha256,
          rg_archive=args.rg_archive, rg_sha256=args.rg_sha256, permission_paths=args.permission_path,
          work_root=args.work_root)


if __name__ == "__main__":
    main()
