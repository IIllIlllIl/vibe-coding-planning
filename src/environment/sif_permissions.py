"""Audited, allowlisted readability repair for a newly built SIF, never its parent.

Only preparation uses this module. Agent/evaluator execution stays non-root.
SquashFS metadata restores ownership/modes lost by unprivileged unpacking.
"""
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess

from src.optimization.hpc.task_batch import atomic_json


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def allowed(path, roots):
    return any(path == root or path.startswith(root + "/") for root in roots)


def validate_roots(roots):
    if not roots or len(set(roots)) != len(roots):
        raise ValueError("permission repair needs distinct explicit paths")
    for root in roots:
        path = PurePosixPath(root)
        if path.is_absolute() or not path.parts or ".." in path.parts or str(path) in {".", "/"}:
            raise ValueError("unsafe permission repair path")
        if any(c in root for c in "\n\r\x00"):
            raise ValueError("unsafe permission repair path")


def mode_bits(text):
    value = 0
    for n, triple in enumerate((text[1:4], text[4:7], text[7:10])):
        bits = (4 if triple[0] == "r" else 0) | (2 if triple[1] == "w" else 0)
        bits |= 1 if triple[2] in "xst" else 0
        value |= bits << ((2 - n) * 3)
    if text[3] in "sS": value |= stat.S_ISUID
    if text[6] in "sS": value |= stat.S_ISGID
    if text[9] in "tT": value |= stat.S_ISVTX
    return value


def parse_listing(text):
    records = {}
    pattern = re.compile(r"^([bcdlps-][rwxStTs-]{9})\s+(\d+)/(\d+)\s+(.+?)\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d:\d\d)\s+(squashfs-root(?:/.*)?)$")
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            if re.match(r"^[bcdlps-][rwxStTs-]{9}\s", line):
                raise ValueError("unsupported SquashFS metadata listing: " + line)
            continue
        mode, uid, gid, size, date, time, name = match.groups()
        path = name.removeprefix("squashfs-root").lstrip("/")
        target = None
        if mode[0] == "l":
            path, target = path.split(" -> ", 1)
        if path in records or ".." in PurePosixPath(path).parts:
            raise ValueError("duplicate or unsafe SquashFS entry")
        records[path] = dict(mode=mode, uid=int(uid), gid=int(gid), size=size,
                             date=date, time=time, target=target)
    if not records or "" not in records:
        raise ValueError("empty or unsupported SquashFS metadata")
    return records


def image_metadata(image, directory, env, *, retain_partition=False, partition_directory=None):
    listed = subprocess.run(["apptainer", "sif", "list", str(image)], env=env,
                             check=True, capture_output=True, text=True).stdout
    partitions = []
    for line in listed.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) >= 5 and "Squashfs" in fields[-1] and "System" in fields[-1]:
            partitions.append(fields[0])
    if len(partitions) != 1 or not partitions[0].isdigit():
        raise ValueError("expected exactly one SIF system SquashFS partition")
    # Audit records remain durable even when the large partition uses node-local
    # build storage. The default preserves existing callers' storage layout.
    filesystem = (partition_directory or directory) / "filesystem.squashfs"
    with filesystem.open("wb") as output:
        subprocess.run(["apptainer", "sif", "dump", partitions[0], str(image)],
                       env=env, check=True, stdout=output)
    try:
        header = subprocess.run(['unsquashfs', '-stat', str(filesystem)], env=env,
                                check=True, capture_output=True, text=True).stdout
        (directory / 'filesystem-superblock.txt').write_text(header)
        if not has_no_xattrs(header):
            raise ValueError('permission-only conversion requires an image without extended attributes')
        result = subprocess.run(["unsquashfs", "-lln", "-full-precision", "-UTC", str(filesystem)], env=env,
                                check=True, capture_output=True, text=True)
        (directory / "filesystem-listing.txt").write_text(result.stdout)
        metadata = parse_listing(result.stdout)
        atomic_json(directory / "filesystem-metadata.json", metadata)
        return metadata
    finally:
        if not retain_partition:
            filesystem.unlink(missing_ok=True)  # Only this reproducible partition copy.


def has_no_xattrs(header):
    return bool(re.search(r'^(Number of xattr ids 0|Xattrs are not stored)$', header, re.MULTILINE))


def unpack(filesystem, destination, env):
    # The build command inserts runtime metadata and uses node-sized extraction
    # buffers. Pure extraction preserves the inventory with bounded memory/CPU.
    subprocess.run(['unsquashfs', '-no-progress', '-processors', '1', '-data-queue', '16',
                    '-frag-queue', '16', '-dest', str(destination), str(filesystem)], env=env, check=True)


def repack_image(parent, destination, sandbox, options, directory, env):
    """Use native SIF commands, preserving non-filesystem object bytes exactly.

    `apptainer build` unconditionally inserts build metadata. This narrow
    permission path instead replaces only the root filesystem object; ordinary
    definition/rg builds retain their established build implementation.
    """
    filesystem = directory / 'repaired.squashfs'
    subprocess.run(['mksquashfs', str(sandbox), str(filesystem), '-noappend', '-processors', '1',
                    *shlex.split(options)], env=env, check=True)
    listed = subprocess.run(['apptainer', 'sif', 'list', str(parent)], env=env,
                            check=True, capture_output=True, text=True).stdout
    subprocess.run(['apptainer', 'sif', 'new', str(destination)], env=env, check=True)
    object_hashes = []
    for line in listed.splitlines():
        fields = [field.strip() for field in line.split('|')]
        if len(fields) != 5 or not fields[0].isdigit():
            continue
        number, group, link, _, kind = fields
        if group == 'NONE': group = '0'
        if link != 'NONE' or kind not in {'Def.FILE', 'JSON.Generic', 'FS (Squashfs/*System/amd64)'}:
            raise ValueError('unsupported SIF descriptor in permission-only conversion: ' + line)
        obj = directory / ('object-' + number)
        if kind.startswith('FS '):
            payload, datatype = filesystem, '4'
            extra = ['--partfs', '1', '--parttype', '2', '--partarch', '2']
        else:
            with obj.open('wb') as output:
                subprocess.run(['apptainer', 'sif', 'dump', number, str(parent)], env=env, stdout=output, check=True)
            payload, datatype, extra = obj, ('1' if kind == 'Def.FILE' else '6'), []
        subprocess.run(['apptainer', 'sif', 'add', str(destination), str(payload), '--datatype', datatype,
                        '--groupid', group, *extra], env=env, check=True)
        if payload != filesystem:
            with (directory / ('roundtrip-object-' + number)).open('wb') as output:
                subprocess.run(['apptainer', 'sif', 'dump', number, str(destination)], env=env, stdout=output, check=True)
            if digest(payload) != digest(directory / ('roundtrip-object-' + number)):
                raise ValueError('SIF non-filesystem object changed')
            object_hashes.append(dict(id=number, type=kind, sha256=digest(payload)))
    filesystem.unlink()  # Reproducible build output, embedded in the new SIF.
    return object_hashes


def content_inventory(root):
    records = {}
    hardlinks = {}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                records[relative] = dict(type="symlink", target=path.readlink().as_posix())
            elif stat.S_ISDIR(mode):
                records[relative] = dict(type="directory")
                visit(path)
            elif stat.S_ISREG(mode):
                records[relative] = dict(type="file", sha256=digest(path), size=path.stat().st_size)
                key = (path.stat().st_dev, path.stat().st_ino)
                records[relative]["hardlink_group"] = hardlinks.setdefault(key, relative)
            else:
                records[relative] = dict(type="special", kind=stat.S_IFMT(mode), rdev=path.lstat().st_rdev)
    visit(root)
    return records


def prepare_repair(sandbox, metadata, roots, directory):
    validate_roots(roots)
    if any(root not in metadata for root in roots):
        raise ValueError("repair path is absent from original image")
    changes = []
    desired = {}
    for relative, item in metadata.items():
        old = mode_bits(item["mode"])
        new = old
        if relative and allowed(relative, roots):
            if item["mode"][0] in {"d", "-"}:
                new |= 0o555 if item["mode"][0] == "d" else 0o444
                path = sandbox / relative
                if path.is_symlink():
                    raise ValueError("permission repair cannot follow a symlink")
                path.chmod(new)
        desired[relative] = new
        if new != old:
            changes.append(dict(path=relative, before=oct(old), after=oct(new)))
    before = content_inventory(sandbox)
    atomic_json(directory / "source-contents.json", before)
    if set(before) != set(metadata) - {""}:
        raise ValueError("unpacking changed the original file inventory: " + repr(sorted(set(before) ^ (set(metadata) - {''}))[:30]))
    atomic_json(directory / "permission-changes.json", changes)
    # Preserve source uid/gid and all other mode bits, not blanket chmod/ownership.
    pseudo = directory / "original-metadata.pseudo"
    lines = []
    for relative, item in metadata.items():
        if not relative:
            continue
        escaped = relative.replace("\\", "\\\\").replace(" ", "\\ ").replace("\t", "\\\t")
        if "\n" in escaped or "\r" in escaped:
            raise ValueError("unsupported pseudo path")
        # Lowercase m is the stable existing-entry modifier, including parent
        # directories. Extraction already retains timestamps, checked below.
        lines.append(f"{escaped} m {desired[relative]:o} {item['uid']} {item['gid']}")
    pseudo.write_text("\n".join(lines) + "\n")
    root = metadata[""]
    stamp = int(datetime.fromisoformat(root['date'] + 'T' + root['time']).replace(tzinfo=timezone.utc).timestamp())
    options = (f"-pf {shlex.quote(str(pseudo))} -pseudo-override -mem 512M -no-xattrs -Xcompression-level 1 "
               f"-root-mode {desired['']:o} -root-uid {root['uid']} -root-gid {root['gid']} -root-time {stamp}")
    return before, desired, changes, options


def verify_repair(before, after, metadata, new_metadata, desired):
    if before != after:
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        raise ValueError("permission-only build changed file contents/topology: " + repr(changed[:30]))
    if set(metadata) != set(new_metadata):
        raise ValueError("permission-only build changed image entry inventory")
    for path, old in metadata.items():
        new = new_metadata[path]
        if (mode_bits(new["mode"]) != desired[path] or new["mode"][0] != old["mode"][0]
                or any(new[k] != old[k] for k in ("uid", "gid", "target", "date", "time"))):
            raise ValueError("unexpected permission/ownership/timestamp change: " + path)
    canonical = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return dict(passed=True, contents_sha256=canonical(before), entries=len(before),
                original_metadata_sha256=canonical(metadata), derived_metadata_sha256=canonical(new_metadata))
