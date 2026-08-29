#!/usr/bin/env python3
"""Acquire a frozen SWE-chat snapshot and repository mirrors on an Iris login node."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tools.login_apptainer_sif_preheat import (  # noqa: E402
    DEFAULT_ULHPC_CONFIG,
    _ssh_command,
    _ssh_config,
)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def content_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def load_manifest(path: Path, purpose: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("purpose") != purpose:
        raise ValueError(f"{path}: invalid schema or purpose")
    observed = content_sha256(value)
    if value.get("content_sha256") != observed:
        raise ValueError(f"{path}: content_sha256 mismatch")
    return value


def load_plan(config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("schema_version") != 1 or raw.get("purpose") != "swe_chat_login_preheat":
        raise ValueError("SWE-chat preheat config has the wrong schema or purpose")
    semantic = raw.get("semantic") or {}
    operational = raw.get("operational") or {}
    source_path = (REPO_ROOT / str(semantic["source_manifest"])).resolve()
    repository_path = (REPO_ROOT / str(semantic["repository_manifest"])).resolve()
    source = load_manifest(source_path, "swe_chat_frozen_source_manifest")
    repositories = load_manifest(
        repository_path, "swe_chat_repository_request_manifest"
    )
    if source["dataset_id"] != repositories["dataset_id"]:
        raise ValueError("dataset and repository manifests disagree on dataset_id")
    if source["revision"] != repositories["revision"]:
        raise ValueError("dataset and repository manifests disagree on revision")
    expected_source_hash = semantic.get("source_manifest_sha256")
    expected_repository_hash = semantic.get("repository_manifest_sha256")
    if expected_source_hash and expected_source_hash != source["content_sha256"]:
        raise ValueError("source manifest does not match configured hash")
    if (
        expected_repository_hash
        and expected_repository_hash != repositories["content_sha256"]
    ):
        raise ValueError("repository manifest does not match configured hash")
    semantic_contract = {
        "schema_version": 1,
        "dataset_id": source["dataset_id"],
        "revision": source["revision"],
        "source_manifest_sha256": source["content_sha256"],
        "repository_manifest_sha256": repositories["content_sha256"],
        "repository_clone_mode": semantic.get("repository_clone_mode", "mirror"),
        "git_lfs_smudge": bool(semantic.get("git_lfs_smudge", False)),
        "recurse_submodules": bool(semantic.get("recurse_submodules", False)),
    }
    if semantic_contract["repository_clone_mode"] != "mirror":
        raise ValueError("v1 supports only repository_clone_mode: mirror")
    if semantic_contract["git_lfs_smudge"]:
        raise ValueError("v1 requires git_lfs_smudge: false")
    if semantic_contract["recurse_submodules"]:
        raise ValueError("v1 requires recurse_submodules: false")
    identity = hashlib.sha256(canonical_bytes(semantic_contract)).hexdigest()
    return {
        "config": raw,
        "source": source,
        "repositories": repositories,
        "semantic_contract": semantic_contract,
        "semantic_identity": identity,
        "operational": operational,
    }


def _remote_program() -> str:
    return r"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

payload = json.load(sys.stdin)
root = Path(payload["remote_root"])
root.mkdir(parents=True, exist_ok=True)
state_path = root / "state.json"
lock_handle = (root / ".writer.lock").open("w")
try:
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    raise SystemExit("another SWE-chat preheat writer owns this identity")

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)

def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_blob_id(path):
    digest = hashlib.sha1()
    size = path.stat().st_size
    digest.update(f"blob {size}\0".encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def verify_dataset(directory):
    observed = []
    expected_paths = set()
    for item in payload["source_manifest"]["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("unsafe source path: " + item["path"])
        expected_paths.add(item["path"])
        path = directory / relative
        if not path.is_file():
            raise RuntimeError("missing dataset file: " + item["path"])
        if path.stat().st_size != item["bytes"]:
            raise RuntimeError("dataset file size mismatch: " + item["path"])
        observed_sha256 = sha256_file(path)
        if item.get("lfs_sha256") and observed_sha256 != item["lfs_sha256"]:
            raise RuntimeError("dataset LFS SHA-256 mismatch: " + item["path"])
        if not item.get("lfs_sha256") and item.get("blob_id"):
            if git_blob_id(path) != item["blob_id"]:
                raise RuntimeError("dataset Git blob mismatch: " + item["path"])
        observed.append({"path": item["path"], "bytes": item["bytes"], "sha256": observed_sha256})
    actual_paths = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and ".cache" not in path.relative_to(directory).parts
    }
    if actual_paths != expected_paths:
        raise RuntimeError("dataset file universe mismatch after download")
    return observed

def classify_failure(error):
    lowered = error.lower()
    if "could not read username for 'https://github.com'" in lowered and "terminal prompts disabled" in lowered:
        return "authentication_or_source_unavailable"
    if "repository not found" in lowered or "not found" in lowered or "error: 404" in lowered:
        return "source_unavailable"
    if "401" in lowered or "unauthorized" in lowered or "gatedrepoerror" in lowered:
        return "authentication_blocked"
    if "no space left" in lowered or "disk quota" in lowered:
        return "disk_blocked"
    if "timed out" in lowered or "timeout" in lowered:
        return "retryable_timeout"
    if any(x in lowered for x in ("could not resolve", "connection reset", "temporary failure", "tls")):
        return "retryable_network"
    return "retryable_failure"

def repository_evidence(path):
    bare = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-bare-repository"],
        capture_output=True, text=True, check=False,
    )
    if bare.returncode != 0 or bare.stdout.strip() != "true":
        raise RuntimeError("repository cache is not a bare Git repository")
    fsck = subprocess.run(
        ["git", "-C", str(path), "fsck", "--connectivity-only"],
        capture_output=True, text=True, check=False,
    )
    if fsck.returncode != 0:
        raise RuntimeError("git fsck failed: " + (fsck.stderr or fsck.stdout)[-2000:])
    refs_result = subprocess.run(
        ["git", "-C", str(path), "for-each-ref", "--format=%(refname)\t%(objectname)"],
        capture_output=True, text=True, check=False,
    )
    if refs_result.returncode != 0:
        raise RuntimeError("git for-each-ref failed")
    refs = sorted(line for line in refs_result.stdout.splitlines() if line)
    head = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    total_bytes = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    return {
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "ref_count": len(refs),
        "refs_sha256": hashlib.sha256(("\n".join(refs) + "\n").encode()).hexdigest(),
        "repository_bytes": total_bytes,
        "connectivity_verified": True,
    }

if state_path.is_file():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {
        "schema_version": 1,
        "status": "running",
        "created_at": now(),
        "semantic_identity": payload["semantic_identity"],
        "semantic_contract": payload["semantic_contract"],
        "dataset": {"status": "pending", "attempts": []},
        "repositories": {
            item["repo_id"]: {
                "index": item["index"], "url": item["url"],
                "status": "pending", "attempts": [],
            }
            for item in payload["repository_manifest"]["requests"]
        },
        "invocations": [],
    }
if state.get("semantic_identity") != payload["semantic_identity"]:
    raise SystemExit("existing SWE-chat preheat state has a different semantic identity")
expected_repositories = {x["repo_id"] for x in payload["repository_manifest"]["requests"]}
if set(state.get("repositories", {})) != expected_repositories:
    raise SystemExit("existing SWE-chat preheat state has a different repository universe")

if payload["operational_policy"]["repository_failure_policy"] != "skip_and_report":
    raise SystemExit("unsupported repository failure policy")
for repo_id, record in state["repositories"].items():
    if record.get("status") not in {"blocked", "retryable_failed"}:
        continue
    previous_status = record["status"]
    previous_category = record.get("failure_category")
    category = classify_failure(record.get("last_error", ""))
    record.update(
        status="skipped",
        failure_category=category,
        skipped_at=now(),
    )
    state.setdefault("operational_reclassifications", []).append(
        {
            "at": now(),
            "repo_id": repo_id,
            "from_status": previous_status,
            "from_failure_category": previous_category,
            "to_status": "skipped",
            "to_failure_category": category,
            "reason": "repository_failure_policy_changed_to_skip_and_report",
        }
    )

invocation = {
    "started_at": now(),
    "downloader_sha256": payload["downloader_sha256"],
    "config_sha256": payload["config_sha256"],
    "operational_policy": payload["operational_policy"],
    "status": "running",
}
state.setdefault("invocations", []).append(invocation)
atomic_json(state_path, state)

dataset = state["dataset"]
if (
    dataset.get("status") != "completed"
    and len(dataset.get("attempts", [])) < payload["operational_policy"]["dataset_max_attempts"]
):
    attempt = {"started_at": now()}
    dataset.setdefault("attempts", []).append(attempt)
    incomplete = root / "dataset.incomplete"
    complete = root / "dataset"
    if not complete.exists():
        incomplete.mkdir(parents=True, exist_ok=True)
    try:
        if complete.exists():
            observed = verify_dataset(complete)
            if incomplete.exists():
                shutil.rmtree(incomplete)
        else:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=payload["semantic_contract"]["dataset_id"],
                repo_type="dataset",
                revision=payload["semantic_contract"]["revision"],
                local_dir=str(incomplete),
                max_workers=payload["operational_policy"]["hf_max_workers"],
                token=os.environ["HF_TOKEN"],
            )
            observed = verify_dataset(incomplete)
            cache = incomplete / ".cache"
            if cache.exists():
                shutil.rmtree(cache)
        observed_manifest = {
            "schema_version": 1,
            "dataset_id": payload["semantic_contract"]["dataset_id"],
            "revision": payload["semantic_contract"]["revision"],
            "source_manifest_sha256": payload["semantic_contract"]["source_manifest_sha256"],
            "files": observed,
        }
        observed_manifest["content_sha256"] = hashlib.sha256(
            (json.dumps(observed_manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).hexdigest()
        atomic_json(root / "manifests" / "dataset-observed-sha256.json", observed_manifest)
        if not complete.exists():
            incomplete.replace(complete)
        attempt.update(status="completed", completed_at=now())
        dataset.update(
            status="completed", completed_at=now(),
            file_count=len(observed),
            total_bytes=sum(item["bytes"] for item in observed),
            observed_manifest_sha256=observed_manifest["content_sha256"],
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        category = classify_failure(error)
        attempt.update(status="failed", category=category, error=error[-2000:], completed_at=now())
        dataset.update(
            status="blocked" if category in {"authentication_blocked", "disk_blocked"} or "mismatch" in error.lower() else "retryable_failed",
            last_error=error[-2000:], failure_category=category, updated_at=now(),
        )
    atomic_json(state_path, state)

if (
    dataset.get("status") == "retryable_failed"
    and len(dataset.get("attempts", [])) >= payload["operational_policy"]["dataset_max_attempts"]
):
    dataset["status"] = "blocked"
    dataset["failure_category"] = "attempts_exhausted"
    atomic_json(state_path, state)

if dataset.get("status") == "completed":
    repository_root = root / "repositories"
    temporary_root = root / "repository_tmp"
    repository_root.mkdir(parents=True, exist_ok=True)
    temporary_root.mkdir(parents=True, exist_ok=True)
    candidates = [
        (repo_id, record)
        for repo_id, record in sorted(state["repositories"].items(), key=lambda item: item[1]["index"])
        if record.get("status") == "pending"
    ][:payload["operational_policy"]["repository_batch_size"]]
    for repo_id, record in candidates:
        owner, name = repo_id.split("/", 1)
        destination = repository_root / owner / f"{name}.git"
        temporary = temporary_root / owner / f"{name}.git.tmp.{os.getpid()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.parent.mkdir(parents=True, exist_ok=True)
        attempt = {"attempt": len(record["attempts"]) + 1, "started_at": now()}
        record["attempts"].append(attempt)
        try:
            if destination.exists():
                evidence = repository_evidence(destination)
            else:
                if temporary.exists():
                    shutil.rmtree(temporary)
                env = os.environ.copy()
                env.update({"GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"})
                result = subprocess.run(
                    ["git", "clone", "--mirror", "--no-hardlinks", record["url"], str(temporary)],
                    capture_output=True, text=True, check=False, env=env,
                    timeout=payload["operational_policy"]["repository_timeout_seconds"],
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout or "git clone failed")[-2000:])
                evidence = repository_evidence(temporary)
                temporary.replace(destination)
            attempt.update(status="completed", completed_at=now())
            record.update(status="completed", completed_at=now(), **evidence)
        except subprocess.TimeoutExpired as exc:
            error = f"timed out after {exc.timeout}s"
            attempt.update(status="failed", category="retryable_timeout", error=error, completed_at=now())
            record.update(status="skipped", failure_category="retryable_timeout", last_error=error, skipped_at=now(), updated_at=now())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            category = classify_failure(error)
            attempt.update(status="failed", category=category, error=error[-2000:], completed_at=now())
            record.update(status="skipped", failure_category=category, last_error=error[-2000:], skipped_at=now(), updated_at=now())
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        atomic_json(state_path, state)
        print(json.dumps({"event": "repository_finished", "repo_id": repo_id, "status": record["status"]}, sort_keys=True), flush=True)

repo_statuses = [item.get("status") for item in state["repositories"].values()]
terminal = {"completed", "skipped", "source_unavailable"}
if dataset.get("status") == "blocked":
    state["status"] = "blocked"
elif dataset.get("status") == "completed" and all(status in terminal for status in repo_statuses):
    state["status"] = "completed_with_repository_skips" if set(repo_statuses) - {"completed"} else "completed"
    state["completed_at"] = now()
    final = {
        "schema_version": 1,
        "status": state["status"],
        "semantic_identity": state["semantic_identity"],
        "semantic_contract": state["semantic_contract"],
        "dataset": state["dataset"],
        "repositories": state["repositories"],
    }
    atomic_json(root / "final_manifest.json", final)
else:
    state["status"] = "running"
state["updated_at"] = now()
invocation.update(status="completed", completed_at=now())
atomic_json(state_path, state)
summary = {
    "event": "swe_chat_preheat_cycle_finished",
    "status": state["status"],
    "dataset_status": state["dataset"].get("status"),
    "repositories_completed": sum(x == "completed" for x in repo_statuses),
    "repositories_skipped": sum(x in {"skipped", "source_unavailable"} for x in repo_statuses),
    "repositories_unavailable": sum(x == "source_unavailable" for x in repo_statuses),
    "repositories_pending": sum(x == "pending" for x in repo_statuses),
    "repositories_blocked": sum(x == "blocked" for x in repo_statuses),
}
print(json.dumps(summary, sort_keys=True), flush=True)
"""


def _payload(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    operational = plan["operational"]
    policy = {
        "hf_max_workers": int(operational.get("hf_max_workers", 4)),
        "dataset_max_attempts": int(operational.get("dataset_max_attempts", 3)),
        "repository_batch_size": int(operational.get("repository_batch_size", 5)),
        "repository_timeout_seconds": int(
            operational.get("repository_timeout_seconds", 3600)
        ),
        "repository_failure_policy": str(
            operational.get("repository_failure_policy", "")
        ),
    }
    numeric_policy = {
        key: value for key, value in policy.items() if isinstance(value, int)
    }
    if any(value < 1 for value in numeric_policy.values()):
        raise ValueError("operational counts and timeouts must be positive")
    if policy["repository_failure_policy"] != "skip_and_report":
        raise ValueError("repository_failure_policy must be skip_and_report")
    return {
        "remote_root": str(operational["remote_root"]),
        "semantic_identity": plan["semantic_identity"],
        "semantic_contract": plan["semantic_contract"],
        "source_manifest": plan["source"],
        "repository_manifest": plan["repositories"],
        "operational_policy": policy,
        "downloader_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
    }


def _run_remote(
    plan: dict[str, Any], config_path: Path, ulhpc_config: Path
) -> tuple[int, dict[str, Any] | None]:
    target, port, key = _ssh_config(ulhpc_config)
    operational = plan["operational"]
    env_file = str(
        operational.get(
            "remote_hf_env_file",
            "~/.config/vibe-coding-planning/huggingface.env",
        )
    )
    remote_python = str(operational.get("remote_python", "python3"))
    if env_file.startswith("~/"):
        env_file_expression = '"$HOME"/' + shlex.quote(env_file[2:])
    else:
        env_file_expression = shlex.quote(env_file)
    inner = (
        "set +x; source "
        + env_file_expression
        + '; test -n "${HF_TOKEN:-}" || exit 2; exec '
        + shlex.quote(remote_python)
        + " -c "
        + shlex.quote(_remote_program())
    )
    command = "bash -lc " + shlex.quote(inner)
    result = subprocess.run(
        _ssh_command(target, port, key, command),
        input=json.dumps(_payload(plan, config_path)),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    summary = None
    for line in reversed(result.stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("event") == "swe_chat_preheat_cycle_finished":
            summary = value
            break
    return result.returncode, summary


def _remote_status(plan: dict[str, Any], ulhpc_config: Path) -> int:
    target, port, key = _ssh_config(ulhpc_config)
    state_path = Path(str(plan["operational"]["remote_root"])) / "state.json"
    code = (
        "from pathlib import Path; import sys; p=Path(sys.argv[1]); "
        'print(p.read_text() if p.is_file() else \'{"status":"missing"}\')'
    )
    command = "python3 -c " + shlex.quote(code) + " " + shlex.quote(str(state_path))
    return subprocess.run(
        _ssh_command(target, port, key, command), check=False
    ).returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--ulhpc-config", type=Path, default=DEFAULT_ULHPC_CONFIG)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--once", action="store_true")
    action.add_argument("--run-until-terminal", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    plan = load_plan(config_path)
    ulhpc_config = args.ulhpc_config.resolve()
    event = {
        "event": "swe_chat_preheat_plan",
        "semantic_identity": plan["semantic_identity"],
        "dataset_id": plan["source"]["dataset_id"],
        "revision": plan["source"]["revision"],
        "files": plan["source"]["file_count"],
        "bytes": plan["source"]["total_bytes"],
        "repositories": plan["repositories"]["requested_count"],
        "remote_root": plan["operational"]["remote_root"],
    }
    print(json.dumps(event, sort_keys=True), flush=True)
    if args.dry_run:
        return 0
    if args.status:
        return _remote_status(plan, ulhpc_config)
    if args.once:
        return _run_remote(plan, config_path, ulhpc_config)[0]

    max_cycles = int(plan["operational"].get("max_cycles", 50))
    max_no_progress = int(plan["operational"].get("max_no_progress_cycles", 3))
    interval = int(plan["operational"].get("check_interval_seconds", 60))
    previous_progress: tuple[Any, ...] | None = None
    no_progress = 0
    for _ in range(max_cycles):
        returncode, summary = _run_remote(plan, config_path, ulhpc_config)
        if returncode != 0 or summary is None:
            return returncode or 2
        if summary["status"] in {
            "completed",
            "completed_with_repository_skips",
            "completed_with_source_exclusions",
        }:
            return 0
        if summary["status"] == "blocked":
            return 2
        progress = (
            summary["dataset_status"],
            summary["repositories_completed"],
            summary["repositories_skipped"],
            summary["repositories_unavailable"],
        )
        no_progress = no_progress + 1 if progress == previous_progress else 0
        previous_progress = progress
        if no_progress >= max_no_progress:
            print(
                "SWE-chat preheat made no progress across bounded cycles",
                file=sys.stderr,
            )
            return 2
        if interval > 0:
            time.sleep(interval)
    print("SWE-chat preheat reached max_cycles before completion", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
