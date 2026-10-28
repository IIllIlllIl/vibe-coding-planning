"""Private metadata and commit checks must not modify scientific evidence."""
from pathlib import Path
import subprocess

import pytest
import yaml

from src.artifact_policy import PRIVATE_ARTIFACT_MARKER, mark_private_artifact_dir, conventional_private_root
from src.optimization.audit import JsonlLogger
from src.optimization.hpc.task_batch import atomic_json
from scripts.tools.check_commit_safety import check


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


def test_metadata_preserves_existing_payload_and_is_inherited(repo):
    private = repo / "custom-results"
    private.mkdir()
    payload = private / "nested" / "plan.json"
    payload.parent.mkdir()
    raw = b'{"plan": "original bytes"}\n'
    payload.write_bytes(raw)
    mark_private_artifact_dir(private)
    mark_private_artifact_dir(private)
    assert payload.read_bytes() == raw
    assert (private / PRIVATE_ARTIFACT_MARKER).is_file()
    assert git(repo, "check-ignore", str(payload)).strip()
    git(repo, "add", "-f", str(payload))
    report = check(repo, environ={})
    assert report["findings"] == [{"file": "custom-results/nested/plan.json", "source": "index",
                                   "detectors": ["marked_private_artifact"]}]


def test_marker_refuses_checkout_root_including_parent_traversal(repo):
    assert mark_private_artifact_dir(repo) is False
    assert mark_private_artifact_dir(repo / "output" / "..") is False
    assert not (repo / PRIVATE_ARTIFACT_MARKER).exists()


@pytest.mark.parametrize("link_layout", ["run", "output_parent"])
def test_marker_supports_persistent_output_links_without_changing_payload(tmp_path, link_layout):
    persistent = tmp_path / "run_state" / "output"
    target = persistent / "dataset" / "run"
    target.mkdir(parents=True)
    raw = b'{"plan": "original bytes"}\n'
    payload = target / "plan.json"
    payload.write_bytes(raw)
    staging = tmp_path / "staging"
    staging.mkdir()
    if link_layout == "run":
        visible = staging / "output" / "dataset" / "run"
        visible.parent.mkdir(parents=True)
        visible.symlink_to(target, target_is_directory=True)
        link = visible
    else:
        link = staging / "output"
        link.symlink_to(persistent, target_is_directory=True)
        visible = link / "dataset" / "run"
    mark_private_artifact_dir(visible)
    mark_private_artifact_dir(visible)
    atomic_json(visible / "nested" / "result.json", {"status": "completed"})
    JsonlLogger(visible / "audit.jsonl").write("accepted")
    assert link.is_symlink() and visible.resolve() == target
    assert payload.read_bytes() == raw
    assert (target / PRIVATE_ARTIFACT_MARKER).read_text() == "private_experiment_artifacts_v1\n"
    assert (target / ".gitignore").read_text().endswith("*\n")
    assert (target / "nested" / "result.json").is_file()
    assert (target / "audit.jsonl").is_file()
    assert not (tmp_path / PRIVATE_ARTIFACT_MARKER).exists()


@pytest.mark.parametrize("unsafe_target", ["checkout", "home", "filesystem_root"])
def test_resolved_link_target_is_checked_before_metadata_writes(repo, unsafe_target):
    target = {"checkout": repo, "home": Path.home(), "filesystem_root": Path("/")}[
        unsafe_target]
    link = repo / "result-link"
    link.symlink_to(target, target_is_directory=True)
    assert mark_private_artifact_dir(link) is False
    assert link.is_symlink()
    assert not (repo / PRIVATE_ARTIFACT_MARKER).exists()


def test_existing_directory_metadata_is_preserved_through_output_link(tmp_path):
    target = tmp_path / "persistent"
    target.mkdir()
    marker, ignore = target / PRIVATE_ARTIFACT_MARKER, target / ".gitignore"
    marker.write_text("existing private metadata\n")
    ignore.write_text("existing ignore policy\n")
    link = tmp_path / "output-link"
    link.symlink_to(target, target_is_directory=True)
    mark_private_artifact_dir(link)
    assert marker.read_text() == "existing private metadata\n"
    assert ignore.read_text() == "existing ignore policy\n"


@pytest.mark.parametrize("filename", [PRIVATE_ARTIFACT_MARKER, ".gitignore"])
def test_marker_io_failure_does_not_block_scientific_writers(tmp_path, monkeypatch, filename):
    original = Path.open
    def metadata_denied(path, *args, **kwargs):
        if path.name == filename:
            raise PermissionError("synthetic metadata-only write failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", metadata_denied)
    root = tmp_path / "output" / "dataset" / "run"
    assert mark_private_artifact_dir(root) is False
    atomic_json(root / "result.json", {"plan": "unchanged", "status": "completed"})
    JsonlLogger(root / "audit.jsonl").write("completed")
    assert (root / "result.json").read_bytes() == b'{\n  "plan": "unchanged",\n  "status": "completed"\n}\n'
    assert (root / "audit.jsonl").is_file()


def test_marker_resolution_failure_is_nonblocking(tmp_path, monkeypatch):
    def unavailable(path, *args, **kwargs):
        raise OSError("synthetic path metadata failure")
    monkeypatch.setattr(Path, "resolve", unavailable)
    assert mark_private_artifact_dir(tmp_path / "results") is False


def test_scientific_output_write_failure_is_not_silently_swallowed(tmp_path, monkeypatch):
    original = Path.open
    def result_denied(path, *args, **kwargs):
        if path.name == "result.json.tmp":
            raise PermissionError("synthetic scientific output failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", result_denied)
    with pytest.raises(PermissionError, match="scientific output failure"):
        atomic_json(tmp_path / "output" / "dataset" / "result.json", {"plan": "original"})


def test_index_marker_remains_effective_after_working_marker_removed(repo):
    folder = repo / "custom"
    mark_private_artifact_dir(folder)
    (folder / "payload.txt").write_text("science")
    git(repo, "add", "-f", str(folder / PRIVATE_ARTIFACT_MARKER), str(folder / "payload.txt"))
    (folder / PRIVATE_ARTIFACT_MARKER).unlink()
    report = check(repo, environ={})
    assert len(report["findings"]) == 2
    assert any(item["detectors"] == ["marked_private_artifact"] for item in report["findings"])


def test_checks_staged_bytes_not_sanitized_working_copy(repo):
    path = repo / "settings.txt"
    secret = "sk-" + "a" * 25
    path.write_text(secret)
    git(repo, "add", "settings.txt")
    path.write_text("clean working copy")
    report = check(repo, environ={})
    assert report["findings"][0]["detectors"] == ["api_key_shape"]
    assert secret not in str(report)
    assert path.read_text() == "clean working copy"


def test_known_environment_value_and_private_key_are_not_printed(repo):
    value = "synthetic-credential-value"
    (repo / "test.txt").write_text(value + "\n-----BEGIN " + "PRIVATE KEY-----")
    git(repo, "add", "test.txt")
    report = check(repo, environ={"PROVIDER_API_KEY": value})
    assert set(report["findings"][0]["detectors"]) == {"private_key", "known_environment_value:PROVIDER_API_KEY"}
    assert value not in str(report)


@pytest.mark.parametrize("name", ["auth.json", "private.env", "arbitrary-result.log", "output/run/a.json"])
def test_private_paths_fail_without_reading_contents(repo, name):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("retained evidence")
    git(repo, "add", "-f", name)
    assert check(repo, environ={})["findings"]


def test_source_and_reviewed_catalog_pass_and_worktree_mode_is_explicit(repo):
    for name in ("source with spaces.py", "output/README.md", "output/catalog.json", "output/archive/README.md"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reviewed content")
        git(repo, "add", "-f", name)
    assert not check(repo, environ={})["findings"]
    (repo / "pending.txt").write_text("sk-" + "b" * 25)
    assert not check(repo, environ={})["findings"]
    assert check(repo, worktree=True, environ={})["findings"][0]["source"] == "worktree"


def test_shared_runtime_writers_mark_standard_result_roots(tmp_path):
    root = tmp_path / "output" / "dataset"
    atomic_json(root / "run" / "output.json", {"scientific": "unchanged"})
    JsonlLogger(tmp_path / "logs" / "run.jsonl").write("accepted")
    assert (root / PRIVATE_ARTIFACT_MARKER).is_file()
    assert (tmp_path / "logs" / PRIVATE_ARTIFACT_MARKER).is_file()
    assert not (tmp_path / "output" / PRIVATE_ARTIFACT_MARKER).exists()
    assert conventional_private_root(tmp_path / "output/archive/README.md") is None
    assert conventional_private_root(tmp_path / "configs/manifest.json") is None


def test_coder_v3_only_clarifies_test_support_submission_role():
    old = yaml.safe_load(Path("configs/prompts/safe_pce_native_codex_code_v2.yaml").read_text())["prompts"]
    new = yaml.safe_load(Path("configs/prompts/safe_pce_native_codex_code_v3.yaml").read_text())["prompts"]
    assert new["code_system"] == old["code_system"]
    boundary = "The Git staging area is the implementation-submission boundary."
    assert new["code_instance"].split(boundary)[1] == old["code_instance"].split(boundary)[1]
    assert "test-support helpers" in new["code_instance"]
    assert "Classify changes by their role" in new["code_instance"]
    assert "pylint" not in new["code_instance"]
    assert "official" not in new["code_instance"]
