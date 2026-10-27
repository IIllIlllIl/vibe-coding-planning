"""Offline, non-mutating review of private experiment logs; never a run gate.

Reports locations and detector names only, not matched text or credential
digests. It does not load auth files, call a model, alter evidence, or certify
that a run is leak-free. Findings and unreadable files require human review.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

from src.optimization.hpc.task_batch import atomic_json


_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "api_key_shape": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "github_token_shape": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "jwt_shape": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "credential_assignment": re.compile(
        r'''(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client[_-]?secret)["'\s]*[:=]\s*["'][^\r\n"']{12,}["']'''),
}
_PRIVATE_ENV_NAME = re.compile(r"(?:^|_)(?:API_KEY|TOKEN|SECRET|PASSWORD)$", re.I)
_EXCLUDED_DIRS = {".git", "workspaces", ".venv", "node_modules"}


def credential_detectors(text: str, *, environ=None, include_assignments=True) -> list[str]:
    """Shared location-only detector labels; never return secret values."""
    names = [name for name, pattern in _PATTERNS.items()
             if (include_assignments or name != "credential_assignment") and pattern.search(text)]
    names.extend("known_environment_value:" + name
                 for name, value in (os.environ if environ is None else environ).items()
                 if _PRIVATE_ENV_NAME.search(name) and len(value) >= 8 and value in text)
    return names


def review(run_dir: Path, *, environ=None):
    """Scan one explicit result root without following links into private state."""
    root = run_dir.resolve(strict=True)
    if not root.is_dir() or root in {Path(root.anchor), Path.home()}:
        raise ValueError("Select a specific experiment result directory")
    report_path = root / "credential_review" / "report.json"
    if report_path.parent.is_symlink():
        raise ValueError("Review report directory must not be a symbolic link")
    findings, unreadable, skipped = [], [], []
    scanned = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        kept = []
        for name in dirs:
            child = base / name
            if name in _EXCLUDED_DIRS or child.is_symlink():
                skipped.append(child.relative_to(root).as_posix())
            else:
                kept.append(name)
        dirs[:] = sorted(kept)
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root).as_posix()
            if path == report_path:
                continue
            if path.is_symlink() or not path.is_file():
                skipped.append(relative)
                continue
            if name in {"auth.json", ".env", "credentials"} or name.endswith(".env"):
                findings.append({"file": relative, "line": None,
                                 "detectors": ["credential_state_file_in_result"]})
                skipped.append(relative)  # Flag its location without loading private state.
                continue
            try:
                with path.open("rb") as handle:
                    if b"\x00" in handle.read(8192):
                        skipped.append(relative)
                        continue
                with path.open(encoding="utf-8", errors="replace") as handle:
                    for line_number, line in enumerate(handle, 1):
                        detectors = credential_detectors(line, environ=environ)
                        if detectors:
                            findings.append({"file": relative, "line": line_number,
                                             "detectors": detectors})
                scanned += 1
            except OSError as exc:
                unreadable.append({"file": relative, "error_type": type(exc).__name__})
    return {"schema_version": 1, "purpose": "post_run_human_review_only",
            "scanned_files": scanned, "findings": findings, "unreadable": unreadable,
            "skipped": skipped, "excluded_directory_names": sorted(_EXCLUDED_DIRS),
            "coverage": "Heuristic candidates and exact values already in the review process environment; "
                        "no private auth files loaded, no symlink traversal, no leak-free certification."}, report_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    report, path = review(args.run_dir)
    atomic_json(path, report)
    print(json.dumps({"report": str(path), "scanned_files": report["scanned_files"],
                      "finding_count": len(report["findings"]), "unreadable_count": len(report["unreadable"])}))
    # A finding is not a score, retry request or automatic experiment failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
