"""SWE-bench official evaluation wrapper.

Calls swebench.harness.run_evaluation with a patch and instance metadata.
"""

from __future__ import annotations

import logging
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

    Uses ``image_name`` field if present, otherwise constructs from
    ``repo`` field as ``swebench/{repo}`` with ``/`` replaced by ``-``
    (Docker image names cannot contain ``/`` in the second segment).

    This function is the single source of truth for image-name derivation.
    Both :func:`evaluate` and ``pipeline.run_instance`` import from here
    to avoid drift.

    TODO: Verify the naming convention against the real build_docker_images.sh
    output. SWE-bench Pro image names may use a different prefix or format.
    """
    if "image_name" in instance_info and instance_info["image_name"]:
        return str(instance_info["image_name"])

    repo = instance_info.get("repo", "")
    if repo:
        return f"swebench/{repo.replace('/', '-')}"

    raise FatalError(
        "Cannot determine Docker image name from instance_info. "
        "Need 'image_name' or 'repo' field."
    )


# Backward-compatible aliases
_get_image_name = derive_image_name
get_image_name = derive_image_name


def evaluate(
    patch: str,
    instance_info: dict[str, Any],
    timeout: int = 300,
) -> dict[str, Any]:
    """Evaluate a patch using the SWE-bench official harness.

    Args:
        patch: Git diff format patch content.
        instance_info: Instance metadata dict (from instance_loader).
            Must contain ``instance_id``, and either ``image_name`` or ``repo``.
        timeout: Per-evaluation timeout in seconds. Pipeline callers should
            pass ``config.evaluator.timeout``. Default 300 retained only for
            direct/unit-test callers; production should always set this
            explicitly to honour user configuration.

    Returns:
        A dict with keys ``resolved``, ``stdout``, ``stderr``, ``log_dir``.

    Raises:
        FatalError: If swebench is not installed or image name cannot be determined.
    """
    swebench = _import_swebench()

    instance_id = instance_info.get("instance_id")
    if not instance_id:
        raise FatalError("instance_info missing 'instance_id' field.")

    image_name = derive_image_name(instance_info)

    logger.info(
        "Running SWE evaluation: instance=%s image=%s timeout=%ss",
        instance_id,
        image_name,
        timeout,
    )

    # swebench.harness.run_evaluation returns (resolved_status, log_dir, report)
    # The exact signature may vary; we wrap it defensively.
    try:
        result = swebench.harness.run_evaluation(
            predictions=[{
                "instance_id": instance_id,
                "model_patch": patch,
                "model_name_or_path": "plan-code-test",
            }],
            run_id=f"eval_{instance_id}",
            timeout=timeout,
        )
    except FatalError:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.error("SWE evaluation failed: %s", exc)
        return {
            "resolved": False,
            "stdout": "",
            "stderr": str(exc),
            "log_dir": "",
        }

    # Defensive parsing of result
    if isinstance(result, tuple) and len(result) >= 2:
        resolved_status = result[0]
        log_dir = result[1] if len(result) > 1 else ""
    elif isinstance(result, dict):
        resolved_status = result.get("resolved", False)
        log_dir = result.get("log_dir", "")
    else:
        resolved_status = False
        log_dir = ""

    resolved = bool(resolved_status)

    return {
        "resolved": resolved,
        "stdout": "",  # run_evaluation does not return stdout directly
        "stderr": "",
        "log_dir": str(log_dir) if log_dir else "",
    }
