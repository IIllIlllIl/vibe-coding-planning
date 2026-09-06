"""Phase-local repository history containment for Pro Plan and Code."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from src.exceptions import FatalError


COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def _git(repository: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _write_evidence(path: Path, value: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    temporary = path / "repository_history_containment.json.tmp"
    destination = path / "repository_history_containment.json"
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(destination)


def materialize_ancestor_only_repository(
    repository: Path,
    base_commit: str,
    *,
    evidence_dir: Path,
    forbidden_commits: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Replace a disposable checkout with a clone containing only base ancestry."""

    if not COMMIT_RE.fullmatch(base_commit):
        raise FatalError("Pro history containment requires a full base commit SHA")
    if any(not COMMIT_RE.fullmatch(value) for value in forbidden_commits):
        raise FatalError("Pro history containment received an invalid forbidden SHA")
    source = repository.resolve()
    candidate = source.with_name(source.name + ".ancestor-only.tmp")
    original = source.with_name(source.name + ".unfiltered.tmp")
    for path in (candidate, original):
        if path.exists():
            shutil.rmtree(path)

    source_ref = "refs/heads/vibe-ancestor-base"
    create_ref = _git(source, "update-ref", source_ref, base_commit)
    if create_ref.returncode != 0:
        raise FatalError(
            "could not create the temporary Pro base ref: "
            + create_ref.stderr[-500:]
        )
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--no-local",
            "--single-branch",
            "--no-tags",
            "--branch",
            "vibe-ancestor-base",
            str(source),
            str(candidate),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        shutil.rmtree(candidate, ignore_errors=True)
        raise FatalError("could not clone Pro base ancestry: " + clone.stderr[-500:])

    checkout = _git(candidate, "checkout", "--detach", base_commit)
    remove_branch = _git(candidate, "branch", "-D", "vibe-ancestor-base")
    remove_remote = _git(candidate, "remote", "remove", "origin")
    if any(
        result.returncode != 0
        for result in (checkout, remove_branch, remove_remote)
    ):
        shutil.rmtree(candidate, ignore_errors=True)
        raise FatalError("could not finalize the Pro ancestor-only checkout")

    head = _git(candidate, "rev-parse", "HEAD")
    status = _git(candidate, "status", "--porcelain=v1", "--untracked-files=all")
    non_ancestors = _git(candidate, "rev-list", "--count", "--all", "--not", base_commit)
    refs = _git(candidate, "for-each-ref", "--format=%(refname)")
    remotes = _git(candidate, "remote")
    alternates = candidate / ".git" / "objects" / "info" / "alternates"
    forbidden_available = []
    for commit in forbidden_commits:
        result = _git(candidate, "cat-file", "-e", f"{commit}^{{commit}}")
        forbidden_available.append(result.returncode == 0)
    evidence = {
        "schema_version": 1,
        "policy": "future_history_inaccessible_v1",
        "base_commit": base_commit,
        "head": head.stdout.strip(),
        "status": status.stdout,
        "non_ancestor_count": int(non_ancestors.stdout.strip())
        if non_ancestors.stdout.strip().isdigit()
        else None,
        "ref_count": len([line for line in refs.stdout.splitlines() if line]),
        "remote_count": len([line for line in remotes.stdout.splitlines() if line]),
        "alternates_present": alternates.is_file(),
        "forbidden_commit_count": len(forbidden_commits),
        "forbidden_commits_available": sum(forbidden_available),
        "clone_transport": "no-local_single-branch_no-tags",
    }
    valid = (
        head.returncode == 0
        and evidence["head"] == base_commit
        and status.returncode == 0
        and not evidence["status"]
        and non_ancestors.returncode == 0
        and evidence["non_ancestor_count"] == 0
        and refs.returncode == 0
        and evidence["ref_count"] == 0
        and remotes.returncode == 0
        and evidence["remote_count"] == 0
        and not evidence["alternates_present"]
        and not any(forbidden_available)
    )
    evidence["verified"] = valid
    _write_evidence(evidence_dir, evidence)
    if not valid:
        shutil.rmtree(candidate, ignore_errors=True)
        raise FatalError("Pro ancestor-only repository verification failed")

    source.rename(original)
    try:
        candidate.rename(source)
    except BaseException:
        original.rename(source)
        raise
    shutil.rmtree(original)
    return evidence


__all__ = ["materialize_ancestor_only_repository"]
