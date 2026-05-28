"""SWE-bench Pro official evaluation wrapper.

Wraps scaleapi/SWE-bench_Pro-os ``eval_with_docker`` so that Pro instances
are evaluated through the same official harness used by the benchmark authors.
This keeps the pipeline symmetric with Verified (which uses swebench's
``run_instance``) and minimises custom evaluation logic.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Path to the official SWE-bench Pro evaluation framework
_OFFICIAL_DIR = Path(__file__).resolve().parents[2] / "third_party" / "swe-bench-pro-os"
_SCRIPTS_DIR = _OFFICIAL_DIR / "run_scripts"


def _ensure_official_deps() -> Any:
    """Lazy import the official eval module; add its path if needed."""
    official_path = str(_OFFICIAL_DIR)
    if official_path not in os.sys.path:
        os.sys.path.insert(0, official_path)
    try:
        from swe_bench_pro_eval import eval_with_docker  # type: ignore[import-untyped]
        return eval_with_docker
    except ImportError as exc:
        raise RuntimeError(
            "SWE-bench Pro official evaluator not found. "
            f"Ensure {_OFFICIAL_DIR} exists and contains swe_bench_pro_eval.py"
        ) from exc


def evaluate_pro_instance(
    patch: str,
    instance_info: dict[str, Any],
    timeout: int = 300,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """Evaluate a single SWE-bench Pro instance using the official harness.

    Args:
        patch: Git diff format patch content.
        instance_info: Instance metadata dict (from instance_loader).
            Must contain all Pro fields: ``dockerhub_tag``,
            ``before_repo_set_cmd``, ``selected_test_files_to_run``,
            ``fail_to_pass``, ``pass_to_pass``, ``base_commit``.
        timeout: Ignored (kept for API symmetry with Verified evaluator).
            The official harness uses its own internal timeout.
        output_dir: Directory for evaluation artefacts.  When ``None`` a
            temporary directory is created and cleaned up after the run.

    Returns:
        A dict compatible with the Verified evaluator's return shape:
        ``resolved``, ``stdout``, ``stderr``, ``log_dir``, ``error_info``, ``report``.
    """
    instance_id = instance_info["instance_id"]
    eval_with_docker = _ensure_official_deps()

    # Build a pandas Series matching the official script's expected input
    sample = pd.Series(instance_info)

    # Ensure required fields have sensible defaults so the official script
    # does not crash on missing keys.
    for key in [
        "before_repo_set_cmd",
        "selected_test_files_to_run",
        "fail_to_pass",
        "pass_to_pass",
        "base_commit",
        "repo",
    ]:
        if key not in sample or pd.isna(sample[key]):
            sample[key] = ""

    workspace_parent = output_dir
    cleanup_parent = False
    if workspace_parent is None:
        workspace_parent = tempfile.mkdtemp(prefix="pro_eval_")
        cleanup_parent = True

    output_path = Path(workspace_parent) / instance_id
    output_path.mkdir(parents=True, exist_ok=True)

    # The official script writes artefacts under output_dir / instance_id.
    # We point it at our workspace so we can read the results back.
    # The official script uses hard-coded relative paths for dockerfiles,
    # so we must run from the official framework root.
    original_cwd = os.getcwd()
    try:
        os.chdir(_OFFICIAL_DIR)
        result = eval_with_docker(
            patch=patch,
            sample=sample,
            output_dir=str(workspace_parent),
            dockerhub_username="jefzda",
            scripts_dir="run_scripts",
            prefix="",
            redo=False,
            block_network=False,
            docker_platform="linux/amd64",
        )
    except Exception as exc:
        logger.error("[%s] Official Pro evaluator raised %s: %s", instance_id, type(exc).__name__, exc)
        return {
            "resolved": False,
            "stdout": "",
            "stderr": f"Official evaluator error: {exc}",
            "log_dir": str(output_path),
            "error_info": f"{type(exc).__name__}: {exc}",
            "report": {},
        }
    finally:
        os.chdir(original_cwd)
        if cleanup_parent:
            import shutil
            shutil.rmtree(workspace_parent, ignore_errors=True)

    if result is None:
        return {
            "resolved": False,
            "stdout": "",
            "stderr": "Official evaluator returned None (see logs)",
            "log_dir": str(output_path),
            "error_info": "eval_with_docker returned None",
            "report": {},
        }

    # The official script returns a dict with key "tests" (list of
    # {"name": str, "status": "PASSED"|"FAILED"|...}).
    tests = result.get("tests", [])
    passed_names = {t["name"] for t in tests if t.get("status") == "PASSED"}

    # Parse fail_to_pass / pass_to_pass (they are stringified JSON lists)
    import ast

    def _parse_tests(raw: Any) -> set[str]:
        if isinstance(raw, (list, set)):
            return set(raw)
        if isinstance(raw, str):
            try:
                return set(ast.literal_eval(raw))
            except Exception:
                return set()
        return set()

    f2p = _parse_tests(instance_info.get("fail_to_pass", []))
    p2p = _parse_tests(instance_info.get("pass_to_pass", []))

    f2p_ok = f2p <= passed_names if f2p else True
    p2p_ok = p2p <= passed_names if p2p else True
    resolved = f2p_ok and p2p_ok

    logger.info(
        "[%s] Pro eval: resolved=%s f2p=%d/%d p2p=%d/%d",
        instance_id,
        resolved,
        len(f2p & passed_names),
        len(f2p),
        len(p2p & passed_names),
        len(p2p),
    )

    # Best-effort stdout/stderr extraction from the official output files
    stdout_text = ""
    stderr_text = ""
    try:
        stdout_file = output_path / "_stdout.log"
        if stdout_file.exists():
            stdout_text = stdout_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        stderr_file = output_path / "_stderr.log"
        if stderr_file.exists():
            stderr_text = stderr_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass

    report = {
        instance_id: {
            "resolved": resolved,
            "tests": tests,
            "passed": list(passed_names),
        }
    }

    return {
        "resolved": resolved,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "log_dir": str(output_path),
        "error_info": None,
        "report": report,
    }
