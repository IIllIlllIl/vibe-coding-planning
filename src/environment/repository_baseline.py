"""Restore an Agent repository to the dataset-declared base revision."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any

from src.exceptions import FatalError


def _run(env: Any, command: str, *, timeout: int) -> dict[str, Any]:
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
    timeout: int = 120,
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
