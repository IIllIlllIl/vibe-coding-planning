"""Tests for PolyBench patch filtering."""

import pytest

from src.evaluator.polybench_patch_policy import apply_polybench_patch_policy
from src.exceptions import TaskError


def _diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_keeps_setup_and_source_file_by_default():
    result = apply_polybench_patch_policy(
        _diff("setup.py") + _diff("src/package/module.py")
    )

    assert result.kept_files == ("setup.py", "src/package/module.py")
    assert result.removed_files == ()
    assert "setup.py" in result.patch


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_module.py",
        "test/test_module.py",
    ],
)
def test_rejects_patch_with_only_forbidden_files(path):
    with pytest.raises(TaskError, match="no allowed submission"):
        apply_polybench_patch_policy(_diff(path))


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "pyproject.toml",
        ".github/workflows/actions.yml",
        "docs/guide.rst",
        "examples/notebook.ipynb",
        "src/package/generated_pb2.py",
    ],
)
def test_keeps_non_test_files_regardless_of_extension(path):
    result = apply_polybench_patch_policy(_diff(path))

    assert result.kept_files == (path,)
    assert result.removed_files == ()


def test_rejects_overlap_with_official_test_patch():
    with pytest.raises(TaskError, match="official test patch"):
        apply_polybench_patch_policy(
            _diff("src/package/module.py"),
            test_patch=_diff("src/package/module.py"),
        )


def test_rejects_non_diff_output():
    with pytest.raises(TaskError, match="not a git patch"):
        apply_polybench_patch_policy("not a patch")
