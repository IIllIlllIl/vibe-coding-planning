"""Check staged Git bytes for private artifacts and likely credentials.

No files are staged, modified, removed, or uploaded. This is a pre-commit
review gate, not a training-time filter or a guarantee of secret detection.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.artifact_policy import PRIVATE_ARTIFACT_MARKER
from scripts.tools.review_credential_exposure import credential_detectors


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True).stdout


def _names(data: bytes) -> set[str]:
    return {os.fsdecode(name) for name in data.split(b"\0") if name}


def _private_reason(relative: Path, root: Path, index_names: set[str]) -> str | None:
    if relative.name == PRIVATE_ARTIFACT_MARKER:
        return "private_artifact_marker"
    for parent in relative.parents:
        marker = parent / PRIVATE_ARTIFACT_MARKER
        if marker.as_posix() in index_names or (root / marker).exists():
            return "marked_private_artifact"
    parts = relative.parts
    reviewed = {"output/README.md", "output/catalog.json", "output/archive/README.md"}
    if parts[0] == "output" and relative.as_posix() not in reviewed:
        return "private_output_path"
    if any(part in {"logs", "trajectories", "patches", "opencode_logs",
                         ".tmp_hpc_smoke", ".ulhpc_submit"} for part in parts[:-1]):
        return "private_output_path"
    if (len(parts) > 2 and parts[0] == ".local"
            and parts[1] in {"hpc-supervisor", "operations", "recovery-review"}):
        return "private_output_path"
    if ".log" in relative.suffixes:
        return "private_log_path"
    if (relative.name in {"auth.json", ".env", "credentials"}
            or relative.name.endswith(".env")):
        return "credential_state_path"
    return None


def check(root: Path, *, worktree: bool = False, environ=None) -> dict:
    """Default: review exactly the index. Optional: also inspect pending edits."""
    root = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip())
    index_names = _names(_git(root, "ls-files", "-z"))
    staged = _names(_git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB", "-z"))
    pending = set()
    if worktree:
        pending = _names(_git(root, "diff", "--name-only", "--diff-filter=ACMRTUXB", "-z"))
        pending |= _names(_git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    findings = []
    checks = [(name, "index") for name in sorted(staged)]
    checks += [(name, "worktree") for name in sorted(pending)]
    for name, source in checks:
        relative = Path(name)
        reason = _private_reason(relative, root, index_names)
        if reason:
            findings.append({"file": name, "source": source, "detectors": [reason]})
            continue  # Do not load private result or credential-state contents.
        if source == "index":
            content = _git(root, "show", ":" + name)
        else:
            path = root / relative
            if path.is_symlink():
                content = os.fsencode(os.readlink(path))
            else:
                content = path.read_bytes()
        # Scan binary blobs too: a private key or a known environment value can
        # occur inside an otherwise non-text artifact. Never emit matched text.
        detectors = credential_detectors(content.decode("utf-8", errors="replace"),
                                         environ=environ, include_assignments=False)
        if detectors:
            findings.append({"file": name, "source": source, "detectors": detectors})
    return {"schema_version": 1, "mode": "index_and_worktree" if worktree else "index",
            "staged_files": len(staged), "pending_files": len(pending),
            "findings": findings,
            "coverage": "Directory markers, established private paths, credential shapes and exact "
                        "values already in the process environment. Human staged-diff review remains required."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--worktree", action="store_true", help="Also review unstaged and untracked nonignored files")
    args = parser.parse_args()
    report = check(args.repo, worktree=args.worktree)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
