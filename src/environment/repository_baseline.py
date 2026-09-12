"""Restore an Agent repository to the dataset-declared base revision."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any

from src.exceptions import FatalError


def _run(env: Any, command: str, *, timeout: int | None) -> dict[str, Any]:
    result = dict(env.execute(command, timeout=timeout))
    return {
        "command": command,
        "returncode": result.get("returncode"),
        "output": str(result.get("output", "")),
    }


def _write_evidence(path: Path, evidence: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    temporary = path / "repository_baseline.json.tmp"
    destination = path / "repository_baseline.json"
    temporary.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(destination)


def restore_repository_to_base(
    env: Any,
    base_commit: str,
    *,
    phase: str,
    evidence_dir: Path,
    timeout: int | None = None,
    prune_future_history: bool = False,
) -> dict[str, Any]:
    """Reset and clean the disposable repository, then verify the result."""

    commit = base_commit.strip()
    if not commit:
        raise FatalError(f"{phase} repository baseline has an empty base_commit")
    quoted_commit = shlex.quote(commit)
    quoted_object = shlex.quote(f"{commit}^{{commit}}")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "declared_base_commit": commit,
        "before": {
            "head": _run(env, "git rev-parse HEAD", timeout=timeout),
            "status": _run(
                env,
                "git status --porcelain=v1 --untracked-files=all",
                timeout=timeout,
            ),
            "unstaged_diff": _run(
                env, "git diff --binary --full-index", timeout=timeout
            ),
            "staged_diff": _run(
                env, "git diff --cached --binary --full-index", timeout=timeout
            ),
        },
    }
    evidence["base_commit_check"] = _run(
        env, f"git cat-file -e {quoted_object}", timeout=timeout
    )
    if evidence["base_commit_check"]["returncode"] != 0:
        _write_evidence(evidence_dir, evidence)
        raise FatalError(
            f"{phase} repository does not contain declared base_commit {commit}"
        )

    evidence["restore"] = _run(
        env,
        f"git reset --hard {quoted_commit} && git clean -fd",
        timeout=timeout,
    )
    if prune_future_history and evidence["restore"]["returncode"] == 0:
        evidence["base_commit_epoch"] = _run(
            env,
            f"git show -s --format=%ct {quoted_commit}",
            timeout=timeout,
        )
        evidence["future_history_prune"] = _run(
            env,
            f"TARGET_EPOCH=$(git show -s --format=%ct {quoted_commit}) && "
            "for remote in $(git remote); do git remote remove \"$remote\" "
            "|| exit 1; done && "
            "for tag in $(git tag -l); do "
            "TAG_EPOCH=$(git log -1 --format=%ct \"$tag\" 2>/dev/null || echo 0); "
            "if [ \"${TAG_EPOCH:-0}\" -gt \"$TARGET_EPOCH\" ]; then "
            "git tag -d \"$tag\" >/dev/null 2>&1 || true; fi; done && "
            "CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD || true); "
            "for branch in $(git for-each-ref --format='%(refname:short)' "
            "refs/heads); do if [ \"$branch\" != \"$CURRENT_BRANCH\" ]; then "
            "git branch -D \"$branch\" >/dev/null 2>&1 || exit 1; fi; done && "
            "git reflog expire --expire=now --all && "
            "git gc --prune=now --aggressive",
            timeout=timeout,
        )
        evidence["future_history_check"] = _run(
            env,
            "git log --all --format='%H %ct'",
            timeout=timeout,
        )
    evidence["after"] = {
        "head": _run(env, "git rev-parse HEAD", timeout=timeout),
        "status": _run(
            env,
            "git status --porcelain=v1 --untracked-files=all",
            timeout=timeout,
        ),
    }
    _write_evidence(evidence_dir, evidence)

    restore = evidence["restore"]
    after_head = evidence["after"]["head"]
    after_status = evidence["after"]["status"]
    if restore["returncode"] != 0:
        raise FatalError(
            f"{phase} repository restore failed: {restore['output'][:500]}"
        )
    if prune_future_history:
        base_epoch_result = evidence["base_commit_epoch"]
        prune = evidence["future_history_prune"]
        check = evidence["future_history_check"]
        if base_epoch_result["returncode"] != 0:
            raise FatalError(
                f"{phase} base-commit timestamp could not be read: "
                f"{base_epoch_result['output'][:500]}"
            )
        if prune["returncode"] != 0:
            raise FatalError(
                f"{phase} future-history prune failed: {prune['output'][:500]}"
            )
        try:
            base_epoch = int(base_epoch_result["output"].strip())
            later_commits = []
            for line in check["output"].splitlines():
                fields = line.rsplit(maxsplit=1)
                if len(fields) != 2:
                    raise ValueError(line)
                if int(fields[1]) > base_epoch:
                    later_commits.append(line)
        except ValueError as exc:
            raise FatalError(
                f"{phase} future-history timestamp check was malformed"
            ) from exc
        if check["returncode"] != 0 or later_commits:
            raise FatalError(
                f"{phase} repository retains commits newer than base: "
                f"{' | '.join(later_commits)[:500]}"
            )
    if after_head["returncode"] != 0:
        raise RuntimeError(f"{phase} repository HEAD could not be read after restore")
    if after_head["output"].strip() != commit:
        raise FatalError(
            f"{phase} repository HEAD does not match declared base_commit {commit}"
        )
    if after_status["returncode"] != 0:
        raise RuntimeError(f"{phase} repository status could not be read after restore")
    if after_status["output"].strip():
        raise FatalError(f"{phase} repository is not clean after base restore")
    return evidence


def verify_repository_at_base(
    env: Any,
    base_commit: str,
    *,
    phase: str,
    evidence_dir: Path,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Verify a prepared repository without changing its files or Git state.

    Official evaluator images may contain tracked preparation changes required
    by their test harness.  Evaluator workspaces are freshly copied from the
    immutable SIF, so resetting them would erase that preparation.  This check
    records the starting state and requires the image to be based at the
    dataset-declared commit, but deliberately performs no reset or clean.
    """

    commit = base_commit.strip()
    if not commit:
        raise FatalError(f"{phase} repository baseline has an empty base_commit")
    quoted_object = shlex.quote(f"{commit}^{{commit}}")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "policy": "preserve_immutable_sif_preparation_v1",
        "declared_base_commit": commit,
        "observed": {
            "head": _run(env, "git rev-parse HEAD", timeout=timeout),
            "status": _run(
                env,
                "git status --porcelain=v1 --untracked-files=all",
                timeout=timeout,
            ),
            "unstaged_diff": _run(
                env, "git diff --binary --full-index", timeout=timeout
            ),
            "staged_diff": _run(
                env, "git diff --cached --binary --full-index", timeout=timeout
            ),
        },
    }
    evidence["base_commit_check"] = _run(
        env, f"git cat-file -e {quoted_object}", timeout=timeout
    )
    _write_evidence(evidence_dir, evidence)

    head = evidence["observed"]["head"]
    status = evidence["observed"]["status"]
    if evidence["base_commit_check"]["returncode"] != 0:
        raise FatalError(
            f"{phase} repository does not contain declared base_commit {commit}"
        )
    if head["returncode"] != 0:
        raise RuntimeError(f"{phase} repository HEAD could not be read")
    if head["output"].strip() != commit:
        raise FatalError(
            f"{phase} repository HEAD does not match declared base_commit {commit}"
        )
    if status["returncode"] != 0:
        raise RuntimeError(f"{phase} repository status could not be read")
    for name in ("unstaged_diff", "staged_diff"):
        if evidence["observed"][name]["returncode"] != 0:
            raise RuntimeError(f"{phase} repository {name} could not be read")
    return evidence
