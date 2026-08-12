"""Patch validation and filtering for SWE-PolyBench submissions."""

from __future__ import annotations

from dataclasses import dataclass
import re
import shlex

from src.exceptions import TaskError


_DIFF_HEADER = re.compile(r"^diff --git ", re.MULTILINE)
_TEST_FILE_PATTERNS = (
    re.compile(r"(^|/)tests?(/|$)"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"(^|/)[^/]*_test\.py$"),
)


@dataclass(frozen=True)
class PolyBenchPatchPolicyResult:
    """Result of applying the PolyBench submission policy."""

    patch: str
    kept_files: tuple[str, ...]
    removed_files: tuple[str, ...]
    test_overlap_files: tuple[str, ...]


def _split_file_diffs(patch: str) -> list[str]:
    starts = [match.start() for match in _DIFF_HEADER.finditer(patch)]
    if not starts:
        return []
    starts.append(len(patch))
    return [patch[starts[i] : starts[i + 1]] for i in range(len(starts) - 1)]


def _target_path(file_diff: str) -> str:
    header = file_diff.splitlines()[0]
    try:
        parts = shlex.split(header)
    except ValueError as exc:
        raise TaskError(f"Malformed git diff header: {header}") from exc
    if len(parts) < 4 or not parts[3].startswith("b/"):
        raise TaskError(f"Malformed git diff header: {header}")
    return parts[3][2:]


def patch_files(patch: str) -> set[str]:
    """Return target paths modified by a git patch."""
    return {_target_path(file_diff) for file_diff in _split_file_diffs(patch)}


def _is_allowed_submission_path(path: str) -> bool:
    return not any(pattern.search(path) for pattern in _TEST_FILE_PATTERNS)


def apply_polybench_patch_policy(
    patch: str,
    *,
    test_patch: str = "",
    allow_empty: bool = False,
    reject_overlap: bool = True,
) -> PolyBenchPatchPolicyResult:
    """Filter test files and reject overlap with official test changes.

    ``allow_empty`` is used by evidence-generation workflows where a submission
    containing only diagnostic tests is itself a valid, scoreable empty
    generation. ``reject_overlap=False`` records source-file overlap for the
    evaluator instead of pre-empting its normal patch-application result.
    Existing callers retain the historical rejecting behavior.
    """
    file_diffs = _split_file_diffs(patch)
    if not file_diffs:
        raise TaskError("Code agent output is not a git patch.")

    kept: list[str] = []
    kept_files: list[str] = []
    removed_files: list[str] = []
    for file_diff in file_diffs:
        path = _target_path(file_diff)
        if _is_allowed_submission_path(path):
            # A blank line at the end of a hunk is still a counted context
            # line. Stripping it corrupts the hunk header's line counts.
            kept.append(file_diff if file_diff.endswith("\n") else file_diff + "\n")
            kept_files.append(path)
        else:
            removed_files.append(path)

    if not kept and not allow_empty:
        raise TaskError(
            "PolyBench patch contains no allowed submission changes after filtering. "
            f"Removed files: {', '.join(sorted(removed_files))}"
        )

    overlap = sorted(set(kept_files) & patch_files(test_patch))
    if overlap and reject_overlap:
        raise TaskError(
            "PolyBench patch modifies files also changed by the official test patch: "
            + ", ".join(overlap)
        )

    return PolyBenchPatchPolicyResult(
        patch="".join(kept),
        kept_files=tuple(kept_files),
        removed_files=tuple(removed_files),
        test_overlap_files=tuple(overlap),
    )
