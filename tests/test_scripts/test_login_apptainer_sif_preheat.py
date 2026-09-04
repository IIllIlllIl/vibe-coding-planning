from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.tools import login_apptainer_sif_preheat


def _config(sif_cache_dir: str = "/scratch/test/sif-cache") -> SimpleNamespace:
    return SimpleNamespace(
        container=SimpleNamespace(
            runtime="apptainer",
            sif_cache_dir=sif_cache_dir,
        )
    )


def test_login_preheat_dry_run_filters_missing_images(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "user: tester\nhost: example.invalid\nport: 2222\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "load_optimization_config",
        lambda path, **kwargs: _config(),
    )
    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "_collect_images",
        lambda config: ["python:3.12-slim", "repo/image:latest"],
    )
    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "_remote_existing_sifs",
        lambda *args: {"python_3.12-slim.sif"},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--config",
            "config.yaml",
            "--ulhpc-config",
            str(ulhpc),
            "--missing-only",
            "--dry-run",
        ],
    )

    assert login_apptainer_sif_preheat.main() == 0

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    assert events[0]["event"] == "login_preheat_plan"
    assert events[0]["image_count"] == 1
    assert events[1] == {
        "event": "selected_image",
        "image": "repo/image:latest",
        "sif": "repo_image_latest.sif",
    }


def test_login_preheat_executes_remote_script_with_scratch_cache(
    tmp_path: Path, monkeypatch
) -> None:
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "user: tester\nhost: example.invalid\nport: 2222\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "load_optimization_config",
        lambda path, **kwargs: _config(),
    )
    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "_collect_images",
        lambda config: ["repo/image:latest"],
    )
    monkeypatch.setattr(login_apptainer_sif_preheat.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--config",
            "config.yaml",
            "--ulhpc-config",
            str(ulhpc),
            "--apptainer-cache-dir",
            "/scratch/test/apptainer-cache-login",
            "--apptainer-tmp-dir",
            "/scratch/test/apptainer-tmp-login",
            "--timeout",
            "123",
        ],
    )

    assert login_apptainer_sif_preheat.main() == 0

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == ["ssh", "-p", "2222"]
    assert command[-2] == "tester@example.invalid"
    payload = json.loads(str(captured["input"]))
    assert payload["images"] == ["repo/image:latest"]
    assert payload["sif_cache_dir"] == "/scratch/test/sif-cache"
    assert payload["apptainer_cache_dir"] == "/scratch/test/apptainer-cache-login"
    assert payload["apptainer_tmp_dir"] == "/scratch/test/apptainer-tmp-login"
    assert payload["timeout"] == 123
    assert payload["cleanup_tmp"] is True
    assert payload["cleanup_apptainer_cache"] is False
    assert payload["provenance_output"] == (
        "/scratch/test/sif-cache/login_preheat_provenance.json"
    )


def test_login_preheat_can_request_apptainer_cache_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "user: tester\nhost: example.invalid\nport: 2222\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "load_optimization_config",
        lambda path, **kwargs: _config(),
    )
    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "_collect_images",
        lambda config: ["repo/image:latest"],
    )
    monkeypatch.setattr(login_apptainer_sif_preheat.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--config",
            "config.yaml",
            "--ulhpc-config",
            str(ulhpc),
            "--cleanup-apptainer-cache",
        ],
    )

    assert login_apptainer_sif_preheat.main() == 0

    payload = json.loads(str(captured["input"]))
    assert payload["cleanup_tmp"] is True
    assert payload["cleanup_apptainer_cache"] is True


def test_login_preheat_requires_positive_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--config",
            "config.yaml",
            "--timeout",
            "0",
        ],
    )

    try:
        login_apptainer_sif_preheat.main()
    except SystemExit as exc:
        assert str(exc) == "--timeout must be positive for login preheat"
    else:
        raise AssertionError("expected SystemExit")


def test_login_preheat_can_audit_existing_remote_image_list(
    tmp_path: Path, monkeypatch
) -> None:
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "user: tester\nhost: example.invalid\nport: 2222\n", encoding="utf-8"
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        remote_command = command[-1]
        if "/remote/images.json" in remote_command:
            return subprocess.CompletedProcess(
                command,
                0,
                '["repo/one:v1.1", "repo/two:v1.1"]\n',
                "",
            )
        if "cache.glob" in remote_command:
            return subprocess.CompletedProcess(
                command,
                0,
                '["repo_one_v1.1.sif"]\n',
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        login_apptainer_sif_preheat,
        "load_optimization_config",
        lambda path, **kwargs: _config(),
    )
    monkeypatch.setattr(login_apptainer_sif_preheat.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--config",
            "config.yaml",
            "--ulhpc-config",
            str(ulhpc),
            "--remote-images-json",
            "/remote/images.json",
            "--existing-only",
        ],
    )

    assert login_apptainer_sif_preheat.main() == 0

    payload = json.loads(str(calls[-1][1]["input"]))
    assert payload["images"] == ["repo/one:v1.1"]


def test_login_preheat_reads_local_frozen_image_list_without_gepa_config(
    tmp_path: Path, monkeypatch
) -> None:
    ulhpc = tmp_path / "ulhpc.yaml"
    ulhpc.write_text(
        "user: tester\nhost: example.invalid\nport: 2222\n", encoding="utf-8"
    )
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps({"images": ["jefzda/sweap-images:one"]}), encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(login_apptainer_sif_preheat.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "login_apptainer_sif_preheat.py",
            "--images-json",
            str(images),
            "--sif-cache-dir",
            "/scratch/test/sif-cache",
            "--ulhpc-config",
            str(ulhpc),
            "--lock-file",
            "/scratch/test/preheat.lock",
        ],
    )

    assert login_apptainer_sif_preheat.main() == 0
    payload = json.loads(str(captured["input"]))
    assert payload["images"] == ["jefzda/sweap-images:one"]
    assert payload["lock_file"] == "/scratch/test/preheat.lock"


def test_remote_preheat_script_records_digest_and_sif_provenance() -> None:
    script = login_apptainer_sif_preheat._remote_script()
    compile(script, "<remote-preheat>", "exec")
    assert "before_and_after_pull_match" in script
    assert '"pull_attested"' in script
    assert '"retrospective"' in script
    assert '"sif_sha256"' in script
    assert "Docker-Content-Digest" in script
    assert "auth.docker.io/token" in script
    assert "registry-1.docker.io/v2" in script
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in script
    assert '"single_writer_lock_busy"' in script
    assert '"registry_manifest_not_found"' in script
    assert '"registry_access_forbidden"' in script
    assert 'provenance["complete"]' in script
    assert 'provenance.setdefault("runs", [])' in script
    assert 'previous.get("status") in {"cached", "pulled"}' in script
    assert 'failed_record["prior_records"]' in script


def test_login_preheat_normalizes_only_oci_repository_case() -> None:
    assert (
        login_apptainer_sif_preheat._normalize_image_ref(
            "ghcr.io/timesler/NameSpace__Repo-1:v1.1"
        )
        == "ghcr.io/timesler/namespace__repo-1:v1.1"
    )
    assert (
        login_apptainer_sif_preheat._normalize_image_ref("python:3.12-slim")
        == "python:3.12-slim"
    )
