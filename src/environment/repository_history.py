"""Reusable, base-bounded Git history artifacts for disposable Agent worktrees."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any, Iterator

from src.exceptions import FatalError


REPOSITORY_HISTORY_POLICY = "base_ancestor_bundle_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_history_key(*, sif_sha256: str, base_commit: str) -> str:
    value = f"{REPOSITORY_HISTORY_POLICY}\0{sif_sha256}\0{base_commit}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RepositoryHistoryCache:
    """Cache Git bundles that expose exactly one base and its ancestors.

    The bundle is generated from a disposable SIF-derived worktree before any
    Agent runs. Selecting one temporary ref at ``base_commit`` lets Git pack
    only that commit's ancestor closure; future branches, tags, reflogs, and
    unreachable objects never enter the artifact. This avoids the high-memory
    whole-repository ``git gc --aggressive`` previously repeated per phase.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def paths(self, *, sif_sha256: str, base_commit: str) -> tuple[Path, Path]:
        key = repository_history_key(
            sif_sha256=sif_sha256,
            base_commit=base_commit,
        )
        directory = self.root / key
        return directory / "repository.bundle", directory / "manifest.json"

    def validate(
        self,
        *,
        sif_sha256: str,
        base_commit: str,
    ) -> tuple[Path, dict[str, Any]] | None:
        bundle, manifest_path = self.paths(
            sif_sha256=sif_sha256,
            base_commit=base_commit,
        )
        if not bundle.is_file() or not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expected = {
            "schema_version": 1,
            "policy": REPOSITORY_HISTORY_POLICY,
            "sif_sha256": sif_sha256,
            "base_commit": base_commit,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            return None
        if manifest.get("bundle_sha256") != _sha256(bundle):
            return None
        return bundle, manifest

    def ensure(
        self,
        *,
        env: Any,
        repository_dir: Path,
        sif_sha256: str,
        base_commit: str,
        instance_id: str,
        timeout: int | None,
    ) -> tuple[Path, dict[str, Any]]:
        existing = self.validate(
            sif_sha256=sif_sha256,
            base_commit=base_commit,
        )
        if existing is not None:
            return existing
        bundle, manifest_path = self.paths(
            sif_sha256=sif_sha256,
            base_commit=base_commit,
        )
        bundle.parent.mkdir(parents=True, exist_ok=True)
        lock_path = bundle.parent / ".build.lock"
        with _exclusive_lock(lock_path):
            existing = self.validate(
                sif_sha256=sif_sha256,
                base_commit=base_commit,
            )
            if existing is not None:
                return existing
            self._build(
                env=env,
                repository_dir=repository_dir,
                bundle=bundle,
                manifest_path=manifest_path,
                sif_sha256=sif_sha256,
                base_commit=base_commit,
                instance_id=instance_id,
                timeout=timeout,
            )
        validated = self.validate(
            sif_sha256=sif_sha256,
            base_commit=base_commit,
        )
        if validated is None:
            raise FatalError("repository history artifact failed post-build validation")
        return validated

    @staticmethod
    def _build(
        *,
        env: Any,
        repository_dir: Path,
        bundle: Path,
        manifest_path: Path,
        sif_sha256: str,
        base_commit: str,
        instance_id: str,
        timeout: int | None,
    ) -> None:
        commit = base_commit.strip()
        if not commit:
            raise FatalError("repository history base_commit is empty")
        quoted_commit = shlex.quote(commit)
        check = env.execute(
            f"git cat-file -e {shlex.quote(commit + '^{commit}')} ",
            timeout=timeout,
        )
        if int(check.get("returncode", 1)) != 0:
            raise FatalError(
                f"repository history source lacks base_commit {base_commit}"
            )
        restore = env.execute(
            f"git reset --hard {quoted_commit} && git clean -fd",
            timeout=timeout,
        )
        if int(restore.get("returncode", 1)) != 0:
            raise FatalError(
                "repository history source restore failed: "
                + str(restore.get("output", ""))[:500]
            )

        relative_bundle = ".vibe_repository_history.bundle"
        build = env.execute(
            "git update-ref refs/vibe/base "
            f"{quoted_commit} && "
            "git -c pack.threads=1 -c pack.windowMemory=64m "
            "-c pack.packSizeLimit=1g bundle create "
            f"{relative_bundle} refs/vibe/base && "
            "git update-ref -d refs/vibe/base",
            timeout=timeout,
        )
        source_bundle = repository_dir / relative_bundle
        if int(build.get("returncode", 1)) != 0 or not source_bundle.is_file():
            raise FatalError(
                "repository history bundle creation failed: "
                + str(build.get("output", ""))[:500]
            )

        bundle_tmp = bundle.with_name(f"{bundle.name}.tmp.{os.getpid()}")
        manifest_tmp = manifest_path.with_name(
            f"{manifest_path.name}.tmp.{os.getpid()}"
        )
        try:
            shutil.move(str(source_bundle), bundle_tmp)
            verify = subprocess.run(
                ["git", "bundle", "verify", str(bundle_tmp)],
                capture_output=True,
                text=True,
                check=False,
            )
            if verify.returncode != 0:
                raise FatalError(
                    "repository history bundle verification failed: "
                    + (verify.stdout + verify.stderr)[:500]
                )
            manifest = {
                "schema_version": 1,
                "policy": REPOSITORY_HISTORY_POLICY,
                "instance_id": instance_id,
                "sif_sha256": sif_sha256,
                "base_commit": base_commit,
                "bundle_sha256": _sha256(bundle_tmp),
                "bundle_bytes": bundle_tmp.stat().st_size,
                "selection": "single_ref_at_base_including_only_ancestor_closure",
                "pack_limits": {
                    "threads": 1,
                    "window_memory": "64m",
                    "pack_size_limit": "1g",
                },
            }
            manifest_tmp.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            bundle_tmp.replace(bundle)
            manifest_tmp.replace(manifest_path)
        finally:
            bundle_tmp.unlink(missing_ok=True)
            manifest_tmp.unlink(missing_ok=True)


def install_repository_history_bundle(
    *,
    repository_dir: Path,
    bundle: Path,
    base_commit: str,
) -> dict[str, Any]:
    """Replace only disposable worktree Git metadata with bounded history."""

    repository_dir = Path(repository_dir)
    temporary = repository_dir.parent / f".{repository_dir.name}.history-install"
    if temporary.exists():
        shutil.rmtree(temporary)
    init = subprocess.run(
        ["git", "init", "--quiet", str(temporary)],
        capture_output=True,
        text=True,
        check=False,
    )
    fetch = subprocess.run(
        [
            "git",
            "-C",
            str(temporary),
            "fetch",
            "--quiet",
            str(bundle),
            "refs/vibe/base:refs/heads/vibe-base",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        if (
            init.returncode != 0
            or fetch.returncode != 0
            or not (temporary / ".git").is_dir()
        ):
            raise FatalError(
                "repository history bundle install failed: "
                + (
                    init.stdout
                    + init.stderr
                    + fetch.stdout
                    + fetch.stderr
                )[:500]
            )
        git_dir = repository_dir / ".git"
        if not git_dir.exists():
            raise FatalError("SIF-derived Agent worktree has no .git directory")
        shutil.rmtree(git_dir)
        shutil.move(str(temporary / ".git"), git_dir)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    commands = [
        ["git", "-C", str(repository_dir), "reset", "--hard", base_commit],
        ["git", "-C", str(repository_dir), "checkout", "--detach", base_commit],
        ["git", "-C", str(repository_dir), "clean", "-fd"],
        [
            "git",
            "-C",
            str(repository_dir),
            "rev-list",
            "--all",
            "--not",
            base_commit,
        ],
    ]
    results = []
    for command in commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            {
                "command": command,
                "returncode": result.returncode,
                "output": result.stdout + result.stderr,
            }
        )
        if result.returncode != 0:
            raise FatalError(
                "repository history install verification failed: "
                + (result.stdout + result.stderr)[:500]
            )
    if results[-1]["output"].strip():
        raise FatalError("repository history bundle exposes non-ancestor commits")
    return {
        "schema_version": 1,
        "policy": REPOSITORY_HISTORY_POLICY,
        "bundle": str(bundle),
        "base_commit": base_commit,
        "checks": results,
    }
