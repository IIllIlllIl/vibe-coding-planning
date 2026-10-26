"""Storage placement must not relax SIF repair acceptance or publication."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.tools import build_codex_isolation_sif as builder
from scripts.tools import repair_sif_permissions as driver
from src.environment import sif_permissions as permissions


@pytest.mark.parametrize("local", [False, True])
def test_permission_builder_storage_and_audits(tmp_path, monkeypatch, local):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    base = tmp_path / "base.sif"
    base.write_bytes(b"parent bytes")
    work_root = tmp_path / "node-local"
    work_root.mkdir()
    operation = tmp_path / "durable-audit"
    metadata = permissions.parse_listing(
        "drwxr-xr-x 0/0 0 2026-01-01 12:00:01 squashfs-root\n"
        "drwxr-xr-x 0/0 0 2026-01-01 12:00:01 squashfs-root/testbed\n"
        "-rw------- 0/0 7 2026-01-01 12:00:01 squashfs-root/testbed/blob\n")
    expected_root = work_root if local else operation
    observed = []

    def image_metadata(image, directory, env, *, retain_partition, partition_directory):
        assert retain_partition
        assert directory.is_relative_to(operation)
        filesystem = (partition_directory or directory) / "filesystem.squashfs"
        assert filesystem.is_relative_to(expected_root)
        filesystem.write_bytes(b"partition")
        newer = copy.deepcopy(metadata)
        if directory.name == "derived":
            newer["testbed/blob"]["mode"] = "-rw-r--r--"
        (directory / "filesystem-metadata.json").write_text(json.dumps(newer))
        observed.append(directory.name)
        return newer

    def unpack(filesystem, sandbox, env):
        assert filesystem.is_relative_to(expected_root)
        assert sandbox.is_relative_to(expected_root)
        (sandbox / "testbed").mkdir(parents=True)
        (sandbox / "testbed/blob").write_bytes(b"payload")

    def repack(parent, destination, sandbox, options, directory, env):
        assert directory.is_relative_to(expected_root)
        assert parent.read_bytes() == b"parent bytes"
        assert parent.is_relative_to(work_root) == local
        destination.write_bytes(b"derived bytes")
        return []

    monkeypatch.setattr(permissions, "image_metadata", image_metadata)
    monkeypatch.setattr(permissions, "unpack", unpack)
    monkeypatch.setattr(permissions, "repack_image", repack)
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="version"))
    monkeypatch.setattr(builder.shutil, "which", lambda *a, **k: str(base))
    report = builder.build(base_sif=base, base_sha256=builder._file_sha256(base),
                           permission_paths=["testbed/blob"], cache=tmp_path / "cache",
                           image="test:readable", operation=operation,
                           work_root=work_root if local else None)
    assert observed == ["original", "derived"]
    assert report["permission_audit"]["passed"]
    assert report["permission_audit"]["changes"] == [dict(path="testbed/blob", before="0o600", after="0o644")]
    assert Path(report["sif_path"]).read_bytes() == b"derived bytes"
    assert base.read_bytes() == b"parent bytes"
    assert (operation / "original/filesystem-metadata.json").is_file()
    assert (operation / "derived/filesystem-metadata.json").is_file()
    assert (operation / "source-contents.json").is_file()
    assert (operation / "permission-audit.json").is_file()
    assert report["build_timings"]["total"]["status"] == "passed"
    assert ("copy_parent_to_work_disk" in report["build_timings"]) == local
    assert not list(expected_root.glob("codex-sif-build-*"))
    assert not list((tmp_path / "cache").glob("*.partial"))
    assert not any(path.name == "filesystem.squashfs" for path in operation.rglob("*"))


def test_cross_filesystem_publication_renames_inside_cache(tmp_path, monkeypatch):
    source = tmp_path / "node-local/image.sif"
    source.parent.mkdir()
    source.write_bytes(b"accepted")
    cache = tmp_path / "durable-cache"
    cache.mkdir()
    target = cache / "image.sif"
    original_replace = Path.replace

    def same_filesystem_only(path, destination):
        assert path.parent == destination.parent == cache
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", same_filesystem_only)
    builder._publish_verified(source, target, hashlib.sha256(b"accepted").hexdigest())
    assert target.read_bytes() == source.read_bytes() == b"accepted"
    assert list(cache.iterdir()) == [target]


@pytest.mark.parametrize("existing", [False, True])
def test_publication_failure_never_publishes_or_overwrites(tmp_path, existing):
    source = tmp_path / "source.sif"
    source.write_bytes(b"accepted")
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / "target.sif"
    if existing:
        target.write_bytes(b"retained")
    with pytest.raises((ValueError, FileExistsError)):
        builder._publish_verified(source, target,
                                  builder._file_sha256(source) if existing else "wrong-hash")
    assert list(cache.iterdir()) == ([target] if existing else [])
    if existing:
        assert target.read_bytes() == b"retained"


def test_local_storage_does_not_change_other_build_modes(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    with pytest.raises(ValueError, match="only for permission"):
        builder.build(cache=tmp_path / "cache", image="test:v1", operation=tmp_path / "audit",
                      work_root=tmp_path)
    assert not (tmp_path / "cache").exists()


def test_corrupted_local_parent_is_rejected_before_build(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    base = tmp_path / "parent.sif"
    base.write_bytes(b"immutable")
    local = tmp_path / "local"
    local.mkdir()
    operation = tmp_path / "audit"

    def corrupt_copy(source, destination):
        destination.write_bytes(b"corrupt")

    monkeypatch.setattr(builder.shutil, "copyfile", corrupt_copy)
    with pytest.raises(ValueError, match="local parent SIF copy hash mismatch"):
        builder.build(base_sif=base, base_sha256=builder._file_sha256(base),
                      permission_paths=["testbed/blob"], cache=tmp_path / "cache",
                      image="test:v1", operation=operation, work_root=local)
    assert base.read_bytes() == b"immutable"
    assert not list(local.iterdir())
    assert not list((tmp_path / "cache").glob("*.sif"))
    assert not (operation / "sif-build.json").exists()
    timings = json.loads((operation / "build-timings.json").read_text())
    assert timings["copy_parent_to_work_disk"]["status"] == "failed"


def test_metadata_partition_is_local_but_audit_is_durable(tmp_path, monkeypatch):
    audit = tmp_path / "audit"
    local = tmp_path / "local"
    audit.mkdir()
    local.mkdir()

    def run(command, **kwargs):
        if command[1:3] == ["sif", "list"]:
            return SimpleNamespace(stdout="3 | 1 | NONE | 0 | FS (Squashfs/*System/amd64)")
        if command[1:3] == ["sif", "dump"]:
            kwargs["stdout"].write(b"partition")
            return SimpleNamespace(stdout="")
        assert Path(command[-1]).parent == local
        if "-stat" in command:
            return SimpleNamespace(stdout="Xattrs are not stored\n")
        return SimpleNamespace(stdout="drwxr-xr-x 0/0 0 2026-01-01 12:00:01 squashfs-root\n")

    monkeypatch.setattr(permissions.subprocess, "run", run)
    assert permissions.image_metadata(tmp_path / "image.sif", audit, {}, partition_directory=local)
    assert (audit / "filesystem-metadata.json").is_file()
    assert (audit / "filesystem-listing.txt").is_file()
    assert not list(local.iterdir())


def test_driver_passes_storage_without_changing_plan_identity(tmp_path, monkeypatch):
    operation = tmp_path / "audit"
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(dict(operation=str(operation), cache=str(tmp_path / "cache"),
        records=[dict(instance_id="test-1", base_sif="/frozen/base.sif", base_sha256="hash",
                      permission_paths=["testbed/blob"], derived_ref="test:v1")])))
    local = tmp_path / "local"

    def build(**kwargs):
        assert kwargs["work_root"] == local
        assert kwargs["operation"].is_relative_to(operation)
        return dict(sif_path="/durable/image.sif", permission_audit=dict(passed=True))

    monkeypatch.setattr(driver, "build", build)
    result = driver.run(plan_path, work_root=local)
    assert result[0]["status"] == "built"
    summary = json.loads((operation / "repair-summary.json").read_text())
    assert summary["plan_sha256"] == driver.file_sha256(plan_path)
    assert summary["model_calls"] == 0
