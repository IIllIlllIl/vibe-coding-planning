from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from scripts.tools import freeze_swe_chat_preheat_inputs as freezer
from scripts.tools import login_swe_chat_preheat as preheat
import scripts.swe_chat_preheat_service as service


REVISION = "f" * 40


def _write_manifest(path: Path, value: dict) -> dict:
    value = dict(value)
    value["content_sha256"] = preheat.content_sha256(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(preheat.canonical_bytes(value))
    return value


def _minimal_plan_files(tmp_path: Path) -> tuple[Path, dict, dict]:
    source = _write_manifest(
        tmp_path / "source.json",
        {
            "schema_version": 1,
            "purpose": "swe_chat_frozen_source_manifest",
            "dataset_id": "SALT-NLP/SWE-chat",
            "revision": REVISION,
            "file_count": 1,
            "total_bytes": 1,
            "files": [
                {
                    "path": "README.md",
                    "bytes": 1,
                    "blob_id": None,
                    "lfs_sha256": None,
                    "lfs_bytes": None,
                }
            ],
        },
    )
    repositories = _write_manifest(
        tmp_path / "repositories.json",
        {
            "schema_version": 1,
            "purpose": "swe_chat_repository_request_manifest",
            "dataset_id": "SALT-NLP/SWE-chat",
            "revision": REVISION,
            "repositories_parquet_sha256": "a" * 64,
            "requested_count": 1,
            "requests": [
                {
                    "index": 0,
                    "repo_id": "owner/repo",
                    "url": "https://github.com/owner/repo",
                }
            ],
        },
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "purpose": "swe_chat_login_preheat",
                "semantic": {
                    "source_manifest": str(tmp_path / "source.json"),
                    "source_manifest_sha256": source["content_sha256"],
                    "repository_manifest": str(tmp_path / "repositories.json"),
                    "repository_manifest_sha256": repositories["content_sha256"],
                    "repository_clone_mode": "mirror",
                    "git_lfs_smudge": False,
                    "recurse_submodules": False,
                },
                "operational": {
                    "remote_root": "/scratch/test/swe-chat",
                    "hf_max_workers": 1,
                    "dataset_max_attempts": 2,
                    "repository_batch_size": 1,
                    "repository_timeout_seconds": 10,
                    "repository_failure_policy": "skip_and_report",
                },
                "supervisor": {"session": "test", "log": "output/test.log"},
            }
        ),
        encoding="utf-8",
    )
    return config, source, repositories


def test_source_manifest_preserves_distinct_git_and_lfs_hashes() -> None:
    siblings = [
        SimpleNamespace(rfilename="README.md", size=7, blob_id="git-blob", lfs=None),
        SimpleNamespace(
            rfilename="data.parquet",
            size=11,
            blob_id="pointer-blob",
            lfs=SimpleNamespace(sha256="lfs-sha256", size=11),
        ),
    ]
    info = SimpleNamespace(sha=REVISION, siblings=list(reversed(siblings)))

    manifest = freezer.build_source_manifest(info, "SALT-NLP/SWE-chat")

    assert [item["path"] for item in manifest["files"]] == [
        "README.md",
        "data.parquet",
    ]
    assert manifest["files"][0]["blob_id"] == "git-blob"
    assert manifest["files"][0]["lfs_sha256"] is None
    assert manifest["files"][1]["lfs_sha256"] == "lfs-sha256"
    assert manifest["content_sha256"] == freezer.content_sha256(manifest)


def test_repository_request_manifest_is_ordered_and_frozen(tmp_path: Path) -> None:
    parquet = tmp_path / "repositories.parquet"
    pq.write_table(
        pa.table(
            {
                "repo_id": ["b/two", "a/one"],
                "url": ["https://github.com/b/two", "https://github.com/a/one"],
            }
        ),
        parquet,
    )

    manifest = freezer.build_repository_request_manifest(
        parquet, dataset_id="SALT-NLP/SWE-chat", revision=REVISION
    )

    assert manifest["requested_count"] == 2
    assert manifest["requests"] == [
        {"index": 0, "repo_id": "b/two", "url": "https://github.com/b/two"},
        {"index": 1, "repo_id": "a/one", "url": "https://github.com/a/one"},
    ]
    assert manifest["repositories_parquet_sha256"] == freezer.file_sha256(parquet)
    assert manifest["content_sha256"] == freezer.content_sha256(manifest)


def test_semantic_identity_ignores_operational_policy(tmp_path: Path) -> None:
    config, _, _ = _minimal_plan_files(tmp_path)
    first = preheat.load_plan(config)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["operational"]["repository_batch_size"] = 9
    raw["operational"]["repository_timeout_seconds"] = 999
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    second = preheat.load_plan(config)

    assert first["semantic_identity"] == second["semantic_identity"]
    assert (
        preheat._payload(first, config)["operational_policy"]
        != preheat._payload(second, config)["operational_policy"]
    )


def test_semantic_identity_changes_with_repository_manifest(tmp_path: Path) -> None:
    config, _, repositories = _minimal_plan_files(tmp_path)
    first = preheat.load_plan(config)
    repositories["requests"][0]["url"] = "https://github.com/owner/renamed"
    repositories["content_sha256"] = preheat.content_sha256(repositories)
    (tmp_path / "repositories.json").write_bytes(preheat.canonical_bytes(repositories))
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["semantic"]["repository_manifest_sha256"] = repositories["content_sha256"]
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    second = preheat.load_plan(config)

    assert first["semantic_identity"] != second["semantic_identity"]


def test_remote_command_expands_home_without_exposing_token(
    tmp_path: Path, monkeypatch
) -> None:
    config, _, _ = _minimal_plan_files(tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["operational"]["remote_hf_env_file"] = "~/.config/project/huggingface.env"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    plan = preheat.load_plan(config)
    observed: dict[str, object] = {}

    monkeypatch.setattr(preheat, "_ssh_config", lambda _: ("host", 22, None))
    monkeypatch.setattr(
        preheat,
        "_ssh_command",
        lambda target, port, key, command: ["ssh", target, command],
    )

    def fake_run(arguments: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        observed["arguments"] = arguments
        observed["stdin"] = kwargs["input"]
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(preheat.subprocess, "run", fake_run)

    returncode, summary = preheat._run_remote(plan, config, tmp_path / "ulhpc.yaml")

    assert returncode == 0
    assert summary is None
    remote_command = str(observed["arguments"][-1])
    assert 'source "$HOME"/.config/project/huggingface.env' in remote_command
    assert "HF_TOKEN" not in str(observed["stdin"])


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def test_remote_program_promotes_dataset_and_mirror_without_network(
    tmp_path: Path,
) -> None:
    source_files = tmp_path / "fake_hub"
    source_files.mkdir()
    readme = b"frozen source\n"
    parquet_bytes = b"not-real-parquet-but-lfs-verified"
    (source_files / "README.md").write_bytes(readme)
    (source_files / "data.parquet").write_bytes(parquet_bytes)
    fake_module = tmp_path / "fake_module"
    fake_module.mkdir()
    (fake_module / "huggingface_hub.py").write_text(
        "from pathlib import Path\n"
        "import os, shutil\n"
        "def snapshot_download(**kwargs):\n"
        "    source = Path(os.environ['FAKE_HUB_SOURCE'])\n"
        "    target = Path(kwargs['local_dir'])\n"
        "    target.mkdir(parents=True, exist_ok=True)\n"
        "    for path in source.iterdir():\n"
        "        shutil.copy2(path, target / path.name)\n"
        "    (target / '.cache' / 'huggingface').mkdir(parents=True, exist_ok=True)\n"
        "    return str(target)\n",
        encoding="utf-8",
    )
    working = tmp_path / "working"
    working.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=working, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=working, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=working, check=True)
    (working / "file.txt").write_text("repository\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=working, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=working, check=True)
    remote_root = tmp_path / "remote"
    source_manifest = {
        "files": [
            {
                "path": "README.md",
                "bytes": len(readme),
                "blob_id": _git_blob_id(readme),
                "lfs_sha256": None,
            },
            {
                "path": "data.parquet",
                "bytes": len(parquet_bytes),
                "blob_id": "unused-lfs-pointer",
                "lfs_sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            },
        ]
    }
    semantic = {
        "dataset_id": "SALT-NLP/SWE-chat",
        "revision": REVISION,
        "source_manifest_sha256": "1" * 64,
        "repository_manifest_sha256": "2" * 64,
        "repository_clone_mode": "mirror",
        "git_lfs_smudge": False,
        "recurse_submodules": False,
    }
    payload = {
        "remote_root": str(remote_root),
        "semantic_identity": hashlib.sha256(
            preheat.canonical_bytes(semantic)
        ).hexdigest(),
        "semantic_contract": semantic,
        "source_manifest": source_manifest,
        "repository_manifest": {
            "requests": [{"index": 0, "repo_id": "owner/repo", "url": str(working)}]
        },
        "operational_policy": {
            "hf_max_workers": 1,
            "dataset_max_attempts": 2,
            "repository_batch_size": 1,
            "repository_timeout_seconds": 30,
            "repository_failure_policy": "skip_and_report",
        },
        "downloader_sha256": "3" * 64,
        "config_sha256": "4" * 64,
    }
    env = os.environ.copy()
    env.update(
        {
            "HF_TOKEN": "test-only-placeholder",
            "FAKE_HUB_SOURCE": str(source_files),
            "PYTHONPATH": str(fake_module),
        }
    )

    result = subprocess.run(
        ["python", "-c", preheat._remote_program()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads((remote_root / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["dataset"]["status"] == "completed"
    assert state["repositories"]["owner/repo"]["status"] == "completed"
    assert (remote_root / "dataset" / "README.md").read_bytes() == readme
    assert not (remote_root / "dataset" / ".cache").exists()
    mirror = remote_root / "repositories" / "owner" / "repo.git"
    assert mirror.is_dir()
    assert (
        subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", "--is-bare-repository"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "true"
    )
    observed = json.loads(
        (remote_root / "manifests" / "dataset-observed-sha256.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["path"] for item in observed["files"]} == {
        "README.md",
        "data.parquet",
    }

    # A crash after atomic promotion but before the state update is recoverable,
    # and changing an operational setting does not change semantic identity.
    state["dataset"]["status"] = "pending"
    (remote_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    payload["operational_policy"]["hf_max_workers"] = 2
    resumed = subprocess.run(
        ["python", "-c", preheat._remote_program()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert resumed.returncode == 0, resumed.stderr
    resumed_state = json.loads((remote_root / "state.json").read_text(encoding="utf-8"))
    assert resumed_state["status"] == "completed"
    assert len(resumed_state["invocations"]) == 2
    assert resumed_state["invocations"][1]["config_sha256"] == "4" * 64
    assert resumed_state["invocations"][1]["operational_policy"]["hf_max_workers"] == 2


def test_remote_program_reclassifies_legacy_failures_and_skips_new_failure(
    tmp_path: Path,
) -> None:
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    semantic = {
        "dataset_id": "SALT-NLP/SWE-chat",
        "revision": REVISION,
        "source_manifest_sha256": "1" * 64,
        "repository_manifest_sha256": "2" * 64,
        "repository_clone_mode": "mirror",
        "git_lfs_smudge": False,
        "recurse_submodules": False,
    }
    semantic_identity = hashlib.sha256(preheat.canonical_bytes(semantic)).hexdigest()
    requests = [
        {
            "index": 0,
            "repo_id": "owner/blocked",
            "url": "https://github.com/owner/blocked",
        },
        {
            "index": 1,
            "repo_id": "owner/retryable",
            "url": "https://github.com/owner/retryable",
        },
        {
            "index": 2,
            "repo_id": "owner/new-failure",
            "url": str(tmp_path / "missing-repository"),
        },
    ]
    inaccessible_error = (
        "RuntimeError: fatal: could not read Username for "
        "'https://github.com': terminal prompts disabled"
    )
    state = {
        "schema_version": 1,
        "status": "blocked",
        "semantic_identity": semantic_identity,
        "semantic_contract": semantic,
        "dataset": {"status": "completed", "attempts": []},
        "repositories": {
            "owner/blocked": {
                "index": 0,
                "url": requests[0]["url"],
                "status": "blocked",
                "failure_category": "attempts_exhausted",
                "last_error": inaccessible_error,
                "attempts": [{"status": "failed"}, {"status": "failed"}],
            },
            "owner/retryable": {
                "index": 1,
                "url": requests[1]["url"],
                "status": "retryable_failed",
                "failure_category": "retryable_failure",
                "last_error": inaccessible_error,
                "attempts": [{"status": "failed"}],
            },
            "owner/new-failure": {
                "index": 2,
                "url": requests[2]["url"],
                "status": "pending",
                "attempts": [],
            },
        },
        "invocations": [],
    }
    (remote_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    payload = {
        "remote_root": str(remote_root),
        "semantic_identity": semantic_identity,
        "semantic_contract": semantic,
        "source_manifest": {"files": []},
        "repository_manifest": {"requests": requests},
        "operational_policy": {
            "hf_max_workers": 1,
            "dataset_max_attempts": 2,
            "repository_batch_size": 3,
            "repository_timeout_seconds": 30,
            "repository_failure_policy": "skip_and_report",
        },
        "downloader_sha256": "3" * 64,
        "config_sha256": "4" * 64,
    }

    result = subprocess.run(
        ["python", "-c", preheat._remote_program()],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    resumed = json.loads((remote_root / "state.json").read_text(encoding="utf-8"))
    assert resumed["status"] == "completed_with_repository_skips"
    assert {item["status"] for item in resumed["repositories"].values()} == {"skipped"}
    assert len(resumed["operational_reclassifications"]) == 2
    assert len(resumed["repositories"]["owner/blocked"]["attempts"]) == 2
    assert len(resumed["repositories"]["owner/retryable"]["attempts"]) == 1
    assert len(resumed["repositories"]["owner/new-failure"]["attempts"]) == 1
    assert (remote_root / "final_manifest.json").is_file()


def test_service_builds_tmux_caffeinate_command(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "purpose": "swe_chat_login_preheat",
                "operational": {"ulhpc_config": str(tmp_path / "ulhpc.yaml")},
                "supervisor": {
                    "session": "swe-chat-test",
                    "log": str(tmp_path / "preheat.log"),
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[:2] == ["tmux", "has-session"]:
            return subprocess.CompletedProcess(arguments, 1, "", "")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(service, "_run", fake_run)
    monkeypatch.setattr(
        service,
        "parse_args",
        lambda: SimpleNamespace(action="start", config=config),
    )

    assert service.main() == 0
    command = calls[-1]
    assert command[:5] == ["tmux", "new-session", "-d", "-s", "swe-chat-test"]
    shell = command[-1]
    assert "exec caffeinate -i -s conda run --no-capture-output -n mini-swe" in shell
    assert "login_swe_chat_preheat.py" in shell
    assert "--run-until-terminal" in shell


def test_tracked_frozen_universe_and_config_are_consistent() -> None:
    config = preheat.load_plan(
        preheat.REPO_ROOT / "configs" / "swe_chat_login_preheat_v1_20260829.yaml"
    )

    assert config["source"]["file_count"] == 5858
    assert config["source"]["total_bytes"] == 12794663592
    assert config["repositories"]["requested_count"] == 205
    assert len(config["repositories"]["requests"]) == 205
    assert len({x["repo_id"] for x in config["repositories"]["requests"]}) == 205
    assert config["operational"]["repository_failure_policy"] == "skip_and_report"
