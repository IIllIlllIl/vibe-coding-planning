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

    Returns:
        A dict with keys ``resolved``, ``stdout``, ``stderr``, ``log_dir``.

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

    logger.info(
        "Running SWE evaluation: instance=%s timeout=%ss",
        instance_id,
        timeout,
    )

    pred = {
        "instance_id": instance_id,
        "model_patch": patch,
        "model_name_or_path": "plan-code-test",
    }
    run_id = f"eval_{instance_id}"

    try:
        client = docker.from_env()
        test_spec = make_test_spec(instance_info, namespace="swebench")

        result = run_instance(
            test_spec=test_spec,
            pred=pred,
            rm_image=False,
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
        return {
            "resolved": False,
            "stdout": "",
            "stderr": str(exc),
            "log_dir": "",
        }

    log_dir = f"logs/run_evaluation/{run_id}/plan-code-test__{instance_id}"
    return {
        "resolved": result.get("resolved", False),
        "stdout": "",
        "stderr": "",
        "log_dir": log_dir,
    }
