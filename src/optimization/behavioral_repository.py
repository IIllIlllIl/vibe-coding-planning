"""Disposable repository materialization for Behavioral Checker calls."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Iterator


class RepositoryMaterializationError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "git command failed").strip()
        raise RepositoryMaterializationError(detail) from exc
    return completed.stdout.strip()


@contextmanager
def materialize_repository_proxy(
    *,
    mirror_path: str | Path,
    proxy_commit: str,
    workspace_root: str | Path,
) -> Iterator[Path]:
    """Yield an isolated clean checkout without mutating shared mirror state."""
    mirror = Path(mirror_path).resolve()
    root = Path(workspace_root).resolve()
    if not mirror.exists():
        raise RepositoryMaterializationError(
            f"repository mirror does not exist: {mirror}"
        )
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="behavioral-checker-", dir=root))
    checkout = temporary / "repo"
    try:
        _git("clone", "--shared", "--no-checkout", str(mirror), str(checkout))
        resolved = _git("rev-parse", f"{proxy_commit}^{{commit}}", cwd=checkout)
        if resolved != proxy_commit:
            raise RepositoryMaterializationError(
                f"proxy commit resolved unexpectedly: {resolved} != {proxy_commit}"
            )
        _git("checkout", "--detach", proxy_commit, cwd=checkout)
        head = _git("rev-parse", "HEAD", cwd=checkout)
        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=checkout)
        status = _git("status", "--porcelain", "--untracked-files=all", cwd=checkout)
        if head != proxy_commit or branch != "HEAD" or status:
            raise RepositoryMaterializationError(
                "materialized repository is not at the requested clean proxy commit"
            )
        yield checkout
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
