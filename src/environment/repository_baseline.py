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


def verify_swebench_evaluator_repository(
    env: Any,
    base_commit: str,
    *,
    phase: str,
    evidence_dir: Path,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Verify an official prepared SWE-bench repository without rewriting it.

    The official Python image builder resets the repository to ``base_commit``,
    performs the repository installation, and then creates one commit with the
    subject ``SWE-bench`` so tracked preparation changes do not pollute the
    submitted patch.  A fresh immutable evaluator SIF may therefore start
    either exactly at ``base_commit`` or at that single preparation child.

    Preserve that prepared HEAD: resetting it can remove test-reporting setup
    required by the official result parser.  The frozen SIF hash authenticates
    the image bytes; this check binds its repository lineage to the dataset
    base and rejects arbitrary descendants or dirty tracked state.
    """

    commit = base_commit.strip()
    if not commit:
        raise FatalError(f"{phase} repository baseline has an empty base_commit")
    quoted_object = shlex.quote(f"{commit}^{{commit}}")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "policy": "official_swebench_prepared_head_v1",
        "declared_base_commit": commit,
        "observed": {
            "head": _run(env, "git rev-parse HEAD", timeout=timeout),
            "head_parents": _run(
                env, "git rev-list --parents -n 1 HEAD", timeout=timeout
            ),
            "head_subject": _run(
                env, "git show -s --format=%s HEAD", timeout=timeout
            ),
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
            "base_to_head_name_status": _run(
                env,
                f"git diff --name-status {shlex.quote(commit)}..HEAD",
                timeout=timeout,
            ),
        },
    }
    evidence["base_commit_check"] = _run(
        env, f"git cat-file -e {quoted_object}", timeout=timeout
    )
    _write_evidence(evidence_dir, evidence)

    head = evidence["observed"]["head"]
    head_parents = evidence["observed"]["head_parents"]
    head_subject = evidence["observed"]["head_subject"]
    if evidence["base_commit_check"]["returncode"] != 0:
        raise FatalError(
            f"{phase} repository does not contain declared base_commit {commit}"
        )
    if head["returncode"] != 0:
        raise RuntimeError(f"{phase} repository HEAD could not be read")
    if head_parents["returncode"] != 0 or head_subject["returncode"] != 0:
        raise RuntimeError(f"{phase} repository HEAD lineage could not be read")
    observed_head = head["output"].strip()
    parent_fields = head_parents["output"].split()
    direct_official_preparation = (
        len(parent_fields) == 2
        and parent_fields[0] == observed_head
        and parent_fields[1] == commit
        and head_subject["output"].strip() == "SWE-bench"
    )
    if observed_head != commit and not direct_official_preparation:
        raise FatalError(
            f"{phase} repository HEAD is neither the declared base_commit nor "
            "its official SWE-bench preparation child"
        )
    for name in (
        "status",
        "unstaged_diff",
        "staged_diff",
        "base_to_head_name_status",
    ):
        if evidence["observed"][name]["returncode"] != 0:
            raise RuntimeError(f"{phase} repository {name} could not be read")
    if evidence["observed"]["unstaged_diff"]["output"].strip():
        raise FatalError(f"{phase} repository has unstaged tracked changes")
    if evidence["observed"]["staged_diff"]["output"].strip():
        raise FatalError(f"{phase} repository has staged changes")
    return evidence
