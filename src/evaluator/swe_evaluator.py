"""SWE-bench official evaluation wrapper.

Calls swebench.harness.run_evaluation with a patch and instance metadata.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.exceptions import FatalError

logger = logging.getLogger(__name__)


def _import_swebench() -> Any:
    """Lazy import swebench to fail gracefully if not installed."""
    try:
        import swebench  # type: ignore[import-untyped]

        return swebench
    except ImportError as exc:
        raise FatalError(
            "swebench is not installed. "
            "Please install it: pip install swebench>=4.1.0"
        ) from exc


def derive_image_name(instance_info: dict[str, Any]) -> str:
    """Derive Docker image name from instance metadata.

    Uses ``image_name`` field if present, otherwise constructs the
    SWE-bench-standard image key:
    ``swebench/sweb.eval.x86_64.{instance_id}:latest`` with ``__``
    replaced by ``_1776_`` (the remote-image namespace convention).

    This function is the single source of truth for image-name derivation.
    Both :func:`evaluate` and ``pipeline.run_instance`` import from here
    to avoid drift.
    """
    if "image_name" in instance_info and instance_info["image_name"]:
        return str(instance_info["image_name"])

    instance_id = instance_info.get("instance_id", "")
    if instance_id:
        key = f"swebench/sweb.eval.x86_64.{instance_id.lower()}:latest"
        return key.replace("__", "_1776_")

    raise FatalError(
        "Cannot determine Docker image name from instance_info. "
        "Need 'image_name' or 'instance_id' field."
    )


# Backward-compatible aliases
_get_image_name = derive_image_name
get_image_name = derive_image_name


def evaluate(
    patch: str,
    instance_info: dict[str, Any],
    timeout: int = 300,
    *,
    run_id_suffix: str = "",
    delete_image: bool = False,
) -> dict[str, Any]:
    """Evaluate a patch using the SWE-bench official harness.

    Calls ``swebench.harness.run_evaluation.run_instance`` directly for a
    single instance, avoiding the overhead of ``run_instances`` batch logic.

    Args:
        patch: Git diff format patch content.
        instance_info: Instance metadata dict (from instance_loader).
            Must contain ``instance_id``, and either ``image_name`` or ``repo``.
        timeout: Per-evaluation timeout in seconds. Pipeline callers should
            pass ``config.evaluator.timeout``. Default 300 retained only for
            direct/unit-test callers; production should always set this
            explicitly to honour user configuration.
        run_id_suffix: Optional suffix appended to the run_id so that
            multiple evaluations of the same instance do not collide on
            cached logs.  Typical value is ``"_r2"`` for round 2.
        delete_image: Whether the official evaluator should remove the
            evaluation image when it finishes. Defaults to False because
            image retention is governed by the configured Docker cache window.

    Returns:
        A dict with keys ``resolved``, ``stdout``, ``stderr``, ``log_dir``,
        and ``report`` (the parsed swebench report dict when available).

    Raises:
        FatalError: If swebench is not installed or image name cannot be determined.
    """
    try:
        import docker
        from swebench.harness.run_evaluation import run_instance
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise FatalError(
            "swebench is not installed. "
            "Please install it: pip install swebench>=4.1.0"
        ) from exc

    instance_id = instance_info.get("instance_id")
    if not instance_id:
        raise FatalError("instance_info missing 'instance_id' field.")

    # Detect dataset type from instance metadata
    dataset_type = instance_info.get("dataset_type", "")
    is_pro = dataset_type == "pro" or "dockerhub_tag" in instance_info
    is_polybench = dataset_type == "polybench"

    logger.info(
        "Running SWE evaluation: instance=%s dataset_type=%s suffix=%s timeout=%ss",
        instance_id,
        dataset_type or "swebench",
        run_id_suffix,
        timeout,
    )

    if is_polybench:
        from src.evaluator.polybench_evaluator import evaluate_polybench_instance
        return evaluate_polybench_instance(
            patch=patch,
            instance_info=instance_info,
            timeout=timeout,
            delete_image=delete_image,
        )

    if is_pro:
        from src.evaluator.pro_official_evaluator import evaluate_pro_instance
        return evaluate_pro_instance(
            patch=patch,
            instance_info=instance_info,
            timeout=timeout,
        )

    pred = {
        "instance_id": instance_id,
        "model_patch": patch,
        "model_name_or_path": "plan-code-test",
    }
    run_id = f"eval_{instance_id}{run_id_suffix}"

    try:
        client = docker.from_env()
        test_spec = make_test_spec(instance_info, namespace="swebench")

        result = run_instance(
            test_spec=test_spec,
            pred=pred,
            rm_image=delete_image,
            force_rebuild=False,
            client=client,
            run_id=run_id,
            timeout=timeout,
            rewrite_reports=False,
        )
    except FatalError:
        raise
    except KeyboardInterrupt:
        raise
    except ImportError as exc:
        raise FatalError(
            "swebench is not installed. "
            "Please install it: pip install swebench>=4.1.0"
        ) from exc
    except Exception as exc:
        logger.error("SWE evaluation failed: %s", exc)
        log_dir_rel = f"logs/run_evaluation/{run_id}/plan-code-test/{instance_id}"
        # Use str(exc) when safe; fall back to reading the log file directly
        # when EvaluationError.__str__() crashes (its internal logger may
        # already be torn down by the time the exception propagates).
        try:
            error_msg = str(exc)
        except Exception:
            error_msg = ""
        if not error_msg:
            try:
                log_file = Path(log_dir_rel).resolve() / "run_instance.log"
                if log_file.exists():
                    log_text = log_file.read_text(encoding="utf-8", errors="replace")
                    for line in reversed(log_text.splitlines()):
                        stripped = line.strip()
                        if stripped and not stripped.startswith("Traceback") and not stripped.startswith("File "):
                            if any(kw in stripped.lower() for kw in ["failed", "error", "patch", "exception", "apply"]):
                                error_msg = stripped[:800]
                                break
            except Exception:
                pass
        if not error_msg:
            error_msg = f"{type(exc).__name__}: SWE evaluation failed (see {log_dir_rel}/run_instance.log)"
        return {
            "resolved": False,
            "stdout": "",
            "stderr": error_msg,
            "log_dir": log_dir_rel,
            "error_info": error_msg,
            "report": {},
        }

    log_dir_rel = f"logs/run_evaluation/{run_id}/plan-code-test/{instance_id}"
    log_dir_abs = Path(log_dir_rel).resolve()

    # Attempt to read the swebench report and test output for richer feedback
    report_data: dict[str, Any] = {}
    stdout_text = ""
    stderr_text = ""
    try:
        report_path = log_dir_abs / "report.json"
        if report_path.exists():
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
            inst_report = report_data.get(instance_id, {})
            stdout_text = inst_report.get("test_output", "")
            stderr_text = inst_report.get("error", "")
    except Exception:
        pass

    # Fallback: read raw test_output.txt
    if not stdout_text:
        try:
            test_output_path = log_dir_abs / "test_output.txt"
            if test_output_path.exists():
                stdout_text = test_output_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    # If swebench returned completed=False (e.g. patch apply failed) but did
    # not raise, extract the failure reason from the log file so that the
    # caller gets actionable feedback.
    if not result.get("completed", True) and not stderr_text:
        try:
            log_file = log_dir_abs / "run_instance.log"
            if log_file.exists():
                log_text = log_file.read_text(encoding="utf-8", errors="replace")
                for line in reversed(log_text.splitlines()):
                    stripped = line.strip()
                    if stripped and not stripped.startswith("Traceback") and not stripped.startswith("File "):
                        if any(kw in stripped.lower() for kw in ["failed", "error", "patch", "exception", "apply"]):
                            stderr_text = stripped[:800]
                            break
        except Exception:
            pass
        if not stderr_text:
            stderr_text = "SWE evaluation completed=False (patch apply or setup failed)"

    # error_info is non-null when evaluation did not complete successfully
    error_info: str | None = None
    if not result.get("completed", True):
        error_info = stderr_text if stderr_text else "SWE evaluation completed=False"

    return {
        "resolved": result.get("resolved", False),
        "stdout": stdout_text if stdout_text else "",
        "stderr": stderr_text if stderr_text else "",
        "log_dir": log_dir_rel,
        "error_info": error_info,
        "report": report_data,
    }
