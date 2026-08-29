#!/usr/bin/env python3
"""Recover the frozen SWE-chat repository subset that requires GitHub auth."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
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


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("schema_version") != 1
        or value.get("purpose") != "swe_chat_repository_recovery_request_manifest"
    ):
        raise ValueError(f"{path}: invalid schema or purpose")
    if value.get("content_sha256") != content_sha256(value):
        raise ValueError(f"{path}: content_sha256 mismatch")
    requests = value.get("requests") or []
    if value.get("requested_count") != len(requests) or not requests:
        raise ValueError("repository recovery manifest has an invalid request count")
    repo_ids = [item.get("repo_id") for item in requests]
    if any(not isinstance(item, str) or item.count("/") != 1 for item in repo_ids):
        raise ValueError("repository recovery manifest has an invalid repo_id")
    if len(repo_ids) != len(set(repo_ids)):
        raise ValueError("repository recovery manifest contains duplicate repo_id")
    return value


def load_plan(config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        raw.get("schema_version") != 1
        or raw.get("purpose") != "swe_chat_login_repository_recovery"
    ):
        raise ValueError("SWE-chat repository recovery config has the wrong schema")
    semantic = raw.get("semantic") or {}
    operational = raw.get("operational") or {}
    manifest_path = Path(str(semantic["request_manifest"])).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = (REPO_ROOT / manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    if semantic.get("request_manifest_sha256") != manifest["content_sha256"]:
        raise ValueError("recovery request manifest does not match configured hash")
    contract = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "revision": manifest["revision"],
        "request_manifest_sha256": manifest["content_sha256"],
        "parent_preheat_semantic_identity": manifest[
            "parent_preheat_semantic_identity"
        ],
        "parent_repository_manifest_sha256": manifest[
            "parent_repository_manifest_sha256"
        ],
        "stage2_manifest_sha256": manifest["stage2_manifest_sha256"],
        "repository_clone_mode": semantic.get("repository_clone_mode", "mirror"),
        "git_lfs_smudge": bool(semantic.get("git_lfs_smudge", False)),
        "recurse_submodules": bool(semantic.get("recurse_submodules", False)),
    }
    if contract["repository_clone_mode"] != "mirror":
        raise ValueError("recovery supports only repository_clone_mode: mirror")
    if contract["git_lfs_smudge"]:
        raise ValueError("recovery requires git_lfs_smudge: false")
    if contract["recurse_submodules"]:
        raise ValueError("recovery requires recurse_submodules: false")
    return {
        "config": raw,
        "manifest": manifest,
        "semantic_contract": contract,
        "semantic_identity": hashlib.sha256(canonical_bytes(contract)).hexdigest(),
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
    raise SystemExit("another SWE-chat repository recovery writer owns this identity")

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)

def sanitize(value):
    text = str(value)
    token = os.environ.get("GITHUB_TOKEN", "")
    return text.replace(token, "<redacted>") if token else text

def classify_failure(error):
    lowered = error.lower()
    if "authentication failed" in lowered or "could not read username" in lowered:
        return "authentication_or_source_unavailable"
    if "repository not found" in lowered or "error: 404" in lowered:
        return "authentication_or_source_unavailable"
    if "timed out" in lowered or "timeout" in lowered:
        return "retryable_timeout"
    if any(x in lowered for x in ("could not resolve", "connection reset", "temporary failure", "tls")):
        return "retryable_network"
    if "no space left" in lowered or "disk quota" in lowered:
        return "disk_blocked"
    return "other_failure"

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
        raise RuntimeError("git fsck failed: " + sanitize(fsck.stderr or fsck.stdout)[-2000:])
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
    return {
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "ref_count": len(refs),
        "refs_sha256": hashlib.sha256(("\n".join(refs) + "\n").encode()).hexdigest(),
        "repository_bytes": sum(x.stat().st_size for x in path.rglob("*") if x.is_file()),
        "connectivity_verified": True,
    }

if state_path.is_file():
    state = json.loads(state_path.read_text(encoding="utf-8"))
else:
    state = {
        "schema_version": 1,
        "purpose": "swe_chat_repository_recovery_state",
        "status": "running",
        "created_at": now(),
        "semantic_identity": payload["semantic_identity"],
        "semantic_contract": payload["semantic_contract"],
        "repositories": {
            item["repo_id"]: {
                "index": item["index"],
                "source_request_index": item["source_request_index"],
                "url": item["url"],
                "eligible_case_count": item["eligible_case_count"],
                "parent_failure_category": item["parent_failure_category"],
                "status": "pending",
                "attempts": [],
            }
            for item in payload["request_manifest"]["requests"]
        },
        "invocations": [],
    }
if state.get("semantic_identity") != payload["semantic_identity"]:
    raise SystemExit("existing recovery state has a different semantic identity")
expected = {x["repo_id"] for x in payload["request_manifest"]["requests"]}
if set(state.get("repositories", {})) != expected:
    raise SystemExit("existing recovery state has a different repository universe")

invocation = {
    "started_at": now(),
    "downloader_sha256": payload["downloader_sha256"],
    "config_sha256": payload["config_sha256"],
    "operational_policy": payload["operational_policy"],
    "status": "running",
}
state.setdefault("invocations", []).append(invocation)
atomic_json(state_path, state)

askpass = root / ".git-askpass"
askpass.write_text(
    "#!/bin/sh\n"
    "case \"$1\" in\n"
    "  *Username*) printf '%s\\n' x-access-token ;;\n"
    "  *Password*) printf '%s\\n' \"$GITHUB_TOKEN\" ;;\n"
    "  *) exit 1 ;;\n"
    "esac\n",
    encoding="utf-8",
)
askpass.chmod(0o700)

try:
    repository_root = root / "repositories"
    temporary_root = root / "repository_tmp"
    repository_root.mkdir(parents=True, exist_ok=True)
    temporary_root.mkdir(parents=True, exist_ok=True)
    for repo_id, record in sorted(state["repositories"].items(), key=lambda x: x[1]["index"]):
        if record.get("status") == "completed":
            continue
        if len(record.get("attempts", [])) >= payload["operational_policy"]["repository_max_attempts"]:
            continue
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
                env.update({
                    "GIT_ASKPASS": str(askpass),
                    "GIT_ASKPASS_REQUIRE": "force",
                    "GIT_LFS_SKIP_SMUDGE": "1",
                    "GIT_TERMINAL_PROMPT": "0",
                })
                result = subprocess.run(
                    ["git", "clone", "--mirror", "--no-hardlinks", record["url"], str(temporary)],
                    capture_output=True, text=True, check=False, env=env,
                    timeout=payload["operational_policy"]["repository_timeout_seconds"],
                )
                if result.returncode != 0:
                    raise RuntimeError(sanitize(result.stderr or result.stdout or "git clone failed")[-2000:])
                evidence = repository_evidence(temporary)
                temporary.replace(destination)
            attempt.update(status="completed", completed_at=now())
            record.update(status="completed", completed_at=now(), **evidence)
        except subprocess.TimeoutExpired as exc:
            error = f"timed out after {exc.timeout}s"
            attempt.update(status="failed", category="retryable_timeout", error=error, completed_at=now())
            record.update(status="skipped", failure_category="retryable_timeout", last_error=error, skipped_at=now())
        except Exception as exc:
            error = sanitize(f"{type(exc).__name__}: {exc}")[-2000:]
            category = classify_failure(error)
            attempt.update(status="failed", category=category, error=error, completed_at=now())
            record.update(status="skipped", failure_category=category, last_error=error, skipped_at=now())
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        atomic_json(state_path, state)
        print(json.dumps({"event": "repository_recovery_finished", "repo_id": repo_id, "status": record["status"]}, sort_keys=True), flush=True)
finally:
    askpass.unlink(missing_ok=True)

statuses = [item.get("status") for item in state["repositories"].values()]
if all(status == "completed" for status in statuses):
    state["status"] = "completed"
elif all(status in {"completed", "skipped"} for status in statuses):
    state["status"] = "completed_with_repository_skips"
else:
    state["status"] = "blocked"
state["completed_at"] = now()
invocation.update(status="completed", completed_at=now())
atomic_json(state_path, state)
final = {
    "schema_version": 1,
    "purpose": "swe_chat_repository_recovery_final_manifest",
    "status": state["status"],
    "semantic_identity": state["semantic_identity"],
    "semantic_contract": state["semantic_contract"],
    "repositories": state["repositories"],
}
atomic_json(root / "final_manifest.json", final)
print(json.dumps({
    "event": "swe_chat_repository_recovery_finished",
    "status": state["status"],
    "repositories_completed": sum(x == "completed" for x in statuses),
    "repositories_skipped": sum(x == "skipped" for x in statuses),
}, sort_keys=True), flush=True)
"""


def _payload(plan: dict[str, Any], config_path: Path) -> dict[str, Any]:
    operational = plan["operational"]
    policy = {
        "repository_timeout_seconds": int(
            operational.get("repository_timeout_seconds", 3600)
        ),
        "repository_max_attempts": int(operational.get("repository_max_attempts", 1)),
        "repository_failure_policy": str(
            operational.get("repository_failure_policy", "")
        ),
    }
    if (
        policy["repository_timeout_seconds"] < 1
        or policy["repository_max_attempts"] < 1
    ):
        raise ValueError("repository timeout and attempts must be positive")
    if policy["repository_failure_policy"] != "skip_and_report":
        raise ValueError("repository_failure_policy must be skip_and_report")
    return {
        "remote_root": str(operational["remote_root"]),
        "semantic_identity": plan["semantic_identity"],
        "semantic_contract": plan["semantic_contract"],
        "request_manifest": plan["manifest"],
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
            "remote_github_env_file",
            "~/.config/vibe-coding-planning/github.env",
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
        + '; test -n "${GITHUB_TOKEN:-}" || exit 2; exec '
        + shlex.quote(remote_python)
        + " -c "
        + shlex.quote(_remote_program())
    )
    result = subprocess.run(
        _ssh_command(target, port, key, "bash -lc " + shlex.quote(inner)),
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
        if value.get("event") == "swe_chat_repository_recovery_finished":
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
    event = {
        "event": "swe_chat_repository_recovery_plan",
        "semantic_identity": plan["semantic_identity"],
        "dataset_id": plan["manifest"]["dataset_id"],
        "revision": plan["manifest"]["revision"],
        "repositories": plan["manifest"]["requested_count"],
        "affected_eligible_cases": plan["manifest"]["affected_eligible_cases"],
        "remote_root": plan["operational"]["remote_root"],
    }
    print(json.dumps(event, sort_keys=True), flush=True)
    if args.dry_run:
        return 0
    ulhpc_config = args.ulhpc_config.resolve()
    if args.status:
        return _remote_status(plan, ulhpc_config)
    returncode, summary = _run_remote(plan, config_path, ulhpc_config)
    if returncode != 0 or summary is None:
        return returncode or 2
    return (
        0
        if summary["status"] in {"completed", "completed_with_repository_skips"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
