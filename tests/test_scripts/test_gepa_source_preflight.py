from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from scripts.internal.gepa_source_preflight import REQUIRED_FILES, validate_gepa_source


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def pinned_worktree(tmp_path):
    source = tmp_path / "gepa-source"
    source.mkdir()
    git(source, "init")
    for name in REQUIRED_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# frozen source\n")
    git(source, "add", ".")
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-m", "dependency")
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init")
    git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(source), "third_party/gepa")
    git(parent, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-am", "pin dependency")
    worktree = tmp_path / "clean-worktree"
    git(parent, "worktree", "add", "--detach", str(worktree), "HEAD")
    return parent, worktree


def test_clean_worktree_is_not_dependency_ready(pinned_worktree):
    _, root = pinned_worktree
    assert git(root, "status", "--porcelain") == ""
    assert "incomplete" in validate_gepa_source(root)


def test_pinned_submodule_initialization_passes_without_network(pinned_worktree):
    _, root = pinned_worktree
    git(root, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--", "third_party/gepa")
    assert git(root, "status", "--porcelain") == ""
    assert validate_gepa_source(root) is None
    source = root / "third_party/gepa"
    git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "wrong revision")
    assert "revision mismatch" in validate_gepa_source(root)


def test_tracked_dependency_changes_fail(pinned_worktree):
    root, _ = pinned_worktree
    (root / "third_party/gepa/src/gepa/api.py").write_text("# modified\n")
    assert "tracked modifications" in validate_gepa_source(root)


def test_export_requires_each_fingerprinted_file(tmp_path):
    for name in REQUIRED_FILES:
        path = tmp_path / "third_party/gepa" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# exported\n")
    assert validate_gepa_source(tmp_path) is None
    (tmp_path / "third_party/gepa/src/gepa/core/state.py").unlink()
    assert "core/state.py" in validate_gepa_source(tmp_path)


def test_supervisor_blocks_before_remote_probe(pinned_worktree, tmp_path, monkeypatch):
    import scripts.hpc_resume_loop as loop
    _, root = pinned_worktree
    monkeypatch.setattr(loop, "REPO_ROOT", root)
    config = SimpleNamespace(batch_script=loop.DEFAULT_BATCH_SCRIPT, require_clean_worktree=False)
    assert "incomplete" in loop._submission_identity_error(config)


def test_non_gepa_workflow_does_not_require_gepa(tmp_path, monkeypatch):
    import scripts.hpc_resume_loop as loop
    config_file = tmp_path / "runtime.yaml"
    config_file.write_text("{}\n")
    config = SimpleNamespace(batch_script=tmp_path / "pcce-wrapper.sh", require_clean_worktree=False,
                             runtime_config=config_file, runtime_config_sha256=loop._sha256(config_file))
    monkeypatch.setattr(loop, "validate_gepa_source", lambda root: pytest.fail("not a GEPA workflow"))
    assert loop._submission_identity_error(config) is None


def test_supervisor_stops_without_remote_probe_or_retries(pinned_worktree, monkeypatch):
    from contextlib import nullcontext
    import scripts.hpc_resume_loop as loop
    _, root = pinned_worktree
    config = SimpleNamespace(submit=True, batch_script=loop.DEFAULT_BATCH_SCRIPT,
        require_clean_worktree=False, slice_time="00:08:00", poll_interval_seconds=120,
        max_runs=20, remote_run_snapshot="unused", target_iterations=1)
    state = {"submissions": 0}
    monkeypatch.setattr(loop, "REPO_ROOT", root)
    monkeypatch.setattr(loop, "_supervisor_lock", lambda config: nullcontext())
    monkeypatch.setattr(loop, "_load_supervisor_state", lambda config: state)
    monkeypatch.setattr(loop, "_save_supervisor_state", lambda config, state: None)
    monkeypatch.setattr(loop, "remote_run_status", lambda config: pytest.fail("must fail before SSH"))
    assert loop.run_loop(config) == 1
    assert state["status"] == "blocked_identity_mismatch"
    assert state["submissions"] == 0
