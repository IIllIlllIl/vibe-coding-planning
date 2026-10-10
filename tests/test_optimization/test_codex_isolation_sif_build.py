import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from scripts.tools import build_codex_isolation_sif as builder


def test_build_requires_compute(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="compute"):
        builder.build(cache=tmp_path, image="test:v1", operation=tmp_path)


def test_hash_mismatch_before_cache_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    base = tmp_path / "base.sif"
    base.write_bytes(b"base")
    archive = tmp_path / "rg.tar.gz"
    archive.write_bytes(b"archive")
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="hash mismatch"):
        builder.build(base_sif=base, base_sha256="wrong", rg_archive=archive,
                      rg_sha256="wrong", cache=cache, image="test:v1",
                      operation=tmp_path / "operation")
    assert not cache.exists()


def test_unprivileged_augmentation_and_atomic_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "123")
    base = tmp_path / "base.sif"
    base.write_bytes(b"base")
    archive = tmp_path / "rg.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("release/rg")
        member.size = 6
        tar.addfile(member, io.BytesIO(b"binary"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["build", "--sandbox"]:
            Path(command[3]).mkdir()
        elif command[1] == "build":
            sandbox = Path(command[-1])
            assert (sandbox / "usr/local/bin/rg").read_bytes() == b"binary"
            assert kwargs["env"]["APPTAINER_MKSQUASHFS_PROCS"] == "1"
            Path(command[2]).write_bytes(b"new-sif")
        return SimpleNamespace(stdout="version")

    monkeypatch.setattr(builder.subprocess, "run", run)
    kwargs = dict(base_sif=base, base_sha256=hashlib.sha256(b"base").hexdigest(),
                  rg_archive=archive, rg_sha256=builder._file_sha256(archive),
                  cache=tmp_path / "cache", image="codex-inspection:rg-v1",
                  operation=tmp_path / "operation")
    report = builder.build(**kwargs)
    assert base.read_bytes() == b"base"
    assert Path(report["sif_path"]).read_bytes() == b"new-sif"
    assert set(report["tool_checks"]) == {"python3", "git", "rg"}
    assert not any("--fakeroot" in command for command in calls)
    with pytest.raises(FileExistsError):
        builder.build(**kwargs)


def test_curator_knows_checker_framework():
    import yaml
    prompt = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v2.yaml").read_text())
    curator = prompt["curator_task"]
    assert "does not receive human acceptance labels" in curator
    assert "accepts it if none triggers" in curator
    assert "finding null and evidence []" in curator
    old = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v1.yaml").read_text())
    for role in ("checker_task", "refiner_task", "checker_instance"):
        assert prompt[role] == old[role]
    reflector = prompt["reflector_task"]
    assert "A correct non-trigger is not automatically helpful" in reflector
    assert "missing checklist coverage" in reflector
    assert "Distinguish defects in the item or guidance from Checker execution errors" in reflector
