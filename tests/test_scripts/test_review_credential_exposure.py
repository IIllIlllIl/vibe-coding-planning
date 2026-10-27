import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.tools.review_credential_exposure import main, review


def test_offline_review_reports_locations_without_changing_or_exposing_values(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    secret = "synthetic-fixture-private-value"
    original = json.dumps({"input_tokens": 512, "trace": secret,
                           "api_key": "sk-test-offline", "token": "ordinary-variable"})
    trajectory = root / "trajectory.json"
    trajectory.write_text(original)
    report, output = review(root, environ={"TEST_API_KEY": secret, "MAX_TOKENS": "512"})
    assert output == root / "credential_review/report.json"
    assert trajectory.read_text() == original
    assert report["findings"] == [{"file": "trajectory.json", "line": 1,
                                     "detectors": ["credential_assignment", "known_environment_value:TEST_API_KEY"]}]
    assert secret not in json.dumps(report) and "sk-test-offline" not in json.dumps(report)
    assert not output.exists()  # Reviewing does not even write the report by itself.


def test_review_does_not_follow_auth_links_or_scan_repository_copies(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (private / "auth.json").write_text('{"access_token": "synthetic-private-authority"}')
    (root / "auth-link.json").symlink_to(private / "auth.json")
    (root / "private-link").symlink_to(private, target_is_directory=True)
    work = root / "workspaces"
    work.mkdir()
    (work / "test.txt").write_text("sk-" + "a" * 30)
    (root / "normal.log").write_text("input_tokens=512; token=data.get('name')\n")
    report, _ = review(root, environ={})
    assert report["findings"] == [] and report["scanned_files"] == 1
    assert set(report["skipped"]) == {"auth-link.json", "private-link", "workspaces"}


def test_cli_findings_do_not_fail_or_rewrite_run(tmp_path, monkeypatch, capsys):
    root = tmp_path / "run"
    root.mkdir()
    original = "-----BEGIN " + "PRIVATE KEY-----\nsynthetic, not an actual key\n"
    (root / "trajectory.log").write_text(original)
    monkeypatch.setattr(sys, "argv", ["review", "--run-dir", str(root)])
    assert main() == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["finding_count"] == 1
    report = json.loads(Path(summary["report"]).read_text())
    assert report["purpose"] == "post_run_human_review_only"
    assert (root / "trajectory.log").read_text() == original
    assert main() == 0  # Its own previous report does not alter review coverage.


def test_auth_state_accidentally_in_results_is_flagged_without_reading(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    authority = root / "auth.json"
    authority.write_text("synthetic private state, not JSON and never parsed")
    original = Path.open
    def guarded(path, *args, **kwargs):
        assert path != authority
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    report, _ = review(root, environ={})
    assert report["findings"] == [{"file": "auth.json", "line": None,
                                    "detectors": ["credential_state_file_in_result"]}]


def test_result_locations_and_stray_log_backstop_are_ignored_without_creating_files():
    root = Path(__file__).resolve().parents[2]
    paths = ["output/example/credential_review/report.json", "logs/example.log", "trajectories/example.json",
             "patches/example.patch", ".local/hpc-supervisor/example.json", "arbitrary-result.log"]
    for path in paths:
        assert subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=root).returncode == 0


@pytest.mark.parametrize("directory", [Path("/"), Path.home()])
def test_review_refuses_broad_machine_roots(directory):
    with pytest.raises(ValueError, match="specific experiment"):
        review(directory, environ={})
