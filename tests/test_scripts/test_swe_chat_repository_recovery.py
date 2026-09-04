from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import yaml

from scripts.tools import login_swe_chat_repository_recovery as recovery
import scripts.swe_chat_preheat_service as service


REVISION = "f" * 40


def _write_plan(tmp_path: Path, *, url: str = "https://github.com/owner/repo") -> Path:
    manifest = {
        "schema_version": 1,
        "purpose": "swe_chat_repository_recovery_request_manifest",
        "dataset_id": "SALT-NLP/SWE-chat",
        "revision": REVISION,
        "parent_preheat_id": "parent",
        "parent_preheat_semantic_identity": "1" * 64,
        "parent_repository_manifest_sha256": "2" * 64,
        "stage2_manifest_sha256": "3" * 64,
        "requested_count": 1,
        "affected_eligible_cases": 2,
        "requests": [
            {
                "index": 0,
                "source_request_index": 7,
                "repo_id": "owner/repo",
                "url": url,
                "eligible_case_count": 2,
                "parent_failure_category": "authentication_or_source_unavailable",
            }
        ],
    }
    manifest["content_sha256"] = recovery.content_sha256(manifest)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(recovery.canonical_bytes(manifest))
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "purpose": "swe_chat_login_repository_recovery",
                "semantic": {
                    "request_manifest": str(manifest_path),
                    "request_manifest_sha256": manifest["content_sha256"],
                    "repository_clone_mode": "mirror",
                    "git_lfs_smudge": False,
                    "recurse_submodules": False,
                },
                "operational": {
                    "remote_root": str(tmp_path / "remote"),
                    "remote_github_env_file": "~/.config/project/github.env",
                    "repository_timeout_seconds": 30,
                    "repository_max_attempts": 1,
                    "repository_failure_policy": "skip_and_report",
                },
                "supervisor": {
                    "session": "recovery-test",
                    "log": str(tmp_path / "recovery.log"),
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def test_recovery_identity_ignores_operational_policy(tmp_path: Path) -> None:
    config = _write_plan(tmp_path)
    first = recovery.load_plan(config)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["operational"]["repository_timeout_seconds"] = 900
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    second = recovery.load_plan(config)

    assert first["semantic_identity"] == second["semantic_identity"]
    assert (
        recovery._payload(first, config)["operational_policy"]
        != recovery._payload(second, config)["operational_policy"]
    )


def test_remote_command_reads_token_only_on_iris(tmp_path: Path, monkeypatch) -> None:
    config = _write_plan(tmp_path)
    plan = recovery.load_plan(config)
    observed: dict[str, object] = {}

    monkeypatch.setattr(recovery, "_ssh_config", lambda _: ("host", 22, None))
    monkeypatch.setattr(
        recovery,
        "_ssh_command",
        lambda target, port, key, command: ["ssh", target, command],
    )

    def fake_run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        observed["arguments"] = arguments
        observed["stdin"] = kwargs["input"]
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)

    returncode, summary = recovery._run_remote(plan, config, tmp_path / "ulhpc.yaml")

    assert returncode == 0
    assert summary is None
    remote_command = str(observed["arguments"][-1])
    assert 'source "$HOME"/.config/project/github.env' in remote_command
    assert 'test -n "${GITHUB_TOKEN:-}"' in remote_command
    assert "GITHUB_TOKEN" not in str(observed["stdin"])


def test_remote_program_promotes_authenticated_mirror_without_secret(
    tmp_path: Path,
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=working, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=working,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=working, check=True)
    (working / "file.txt").write_text("repository\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=working, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=working, check=True)
    config = _write_plan(tmp_path, url=str(working))
    plan = recovery.load_plan(config)
    payload = recovery._payload(plan, config)
    token = "test-only-secret-placeholder"
    env = os.environ.copy()
    env["GITHUB_TOKEN"] = token

    result = subprocess.run(
        ["python", "-c", recovery._remote_program()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    remote_root = Path(payload["remote_root"])
    state_bytes = (remote_root / "state.json").read_bytes()
    final_bytes = (remote_root / "final_manifest.json").read_bytes()
    assert token.encode() not in state_bytes + final_bytes
    assert not (remote_root / ".git-askpass").exists()
    state = json.loads(state_bytes)
    assert state["status"] == "completed"
    assert state["repositories"]["owner/repo"]["status"] == "completed"
    mirror = remote_root / "repositories" / "owner" / "repo.git"
    assert (
        subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", "--is-bare-repository"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "true"
    )


def test_service_dispatches_repository_recovery(tmp_path: Path) -> None:
    config = _write_plan(tmp_path)

    _, _, _, entrypoint = service._load_config(config)

    assert entrypoint == service.RECOVERY_SCRIPT


def test_tracked_recovery_universe_is_frozen() -> None:
    plan = recovery.load_plan(
        recovery.REPO_ROOT / "configs" / "swe_chat_repository_recovery_v1_20260829.yaml"
    )

    assert plan["manifest"]["requested_count"] == 2
    assert plan["manifest"]["affected_eligible_cases"] == 10
    assert {item["repo_id"] for item in plan["manifest"]["requests"]} == {
        "BIDEquity/outbid-dirigent",
        "matthsena/reef-coder",
    }
    assert plan["operational"]["repository_failure_policy"] == "skip_and_report"


def test_repository_availability_cleaning_is_derived_from_stage2() -> None:
    frozen_root = (
        recovery.REPO_ROOT
        / "configs"
        / "frozen_swe_chat_cleaning"
        / "f66cca95b14caaa4177f7ed5eaa424608dadcffa"
    )
    stage2 = json.loads(
        (frozen_root / "stage2-first-plan-slice-v1-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    cleaning = json.loads(
        (frozen_root / "repository-availability-cleaning-v1-manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert cleaning["content_sha256"] == recovery.content_sha256(cleaning)
    assert cleaning["stage2_manifest_sha256"] == stage2["content_sha256"]
    stage2_eligible = {
        x["case_id"]: x for x in stage2["cases"] if x["status"] == "eligible"
    }
    excluded = {x["case_id"]: x for x in cleaning["excluded_cases"]}
    assert len(stage2_eligible) == 141
    assert len(excluded) == 10
    assert set(excluded) < set(stage2_eligible)
    assert {x["repo_id"] for x in excluded.values()} == {
        "BIDEquity/outbid-dirigent",
        "matthsena/reef-coder",
    }
    assert all(x["reason"] == "repository_not_found" for x in excluded.values())
    assert cleaning["counts"] == {
        "stage2_eligible_cases": 141,
        "repository_unavailable_exclusions": 10,
        "optimization_eligible_cases": 131,
        "labels_before_repository_cleaning": {"ACCEPT": 57, "DO_NOT_ACCEPT": 84},
        "labels_after_repository_cleaning": {"ACCEPT": 54, "DO_NOT_ACCEPT": 77},
    }
