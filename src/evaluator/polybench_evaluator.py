"""SWE-PolyBench evaluation wrapper.

Wraps the official poly_bench_evaluation harness for per-instance evaluation.
This evaluator runs PolyBench's Docker-based test execution inside the
pipeline's evaluation phase, reusing the official parser and scoring logic.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Any

import docker

from src.environment.docker_env import is_docker_storage_error
from src.exceptions import FatalError

logger = logging.getLogger(__name__)

_POLYBENCH_IMAGE_TAGS = ("v1.1", "v1.0", "latest")


def _import_polybench() -> Any:
    """Lazy import PolyBench evaluation modules."""
    try:
        from poly_bench_evaluation.docker_utils import DockerManager
        from poly_bench_evaluation.polybench_data import PolyBenchInstance
        from poly_bench_evaluation.scoring import (
            instance_level_scoring,
            store_instance_level_output,
        )
        from poly_bench_evaluation.constants import (
            DEFAULT_TIMEOUT,
            REPO_TO_PARSER_CLASS,
        )

        return {
            "DockerManager": DockerManager,
            "PolyBenchInstance": PolyBenchInstance,
            "instance_level_scoring": instance_level_scoring,
            "store_instance_level_output": store_instance_level_output,
            "DEFAULT_TIMEOUT": DEFAULT_TIMEOUT,
            "REPO_TO_PARSER_CLASS": REPO_TO_PARSER_CLASS,
        }
    except ImportError as exc:
        raise FatalError(
            "poly_bench_evaluation is not installed. "
            "Please install it: pip install -e /path/to/SWE-PolyBench"
        ) from exc


def _try_pull_prebuilt_image_with_fallback(
    docker_manager: Any,
    instance_id: str,
    tags: tuple[str, ...] = _POLYBENCH_IMAGE_TAGS,
) -> bool:
    """Pull a PolyBench image, accepting older official image tags.

    The official DockerManager supports a single tag per pull attempt.
    In practice, some public GHCR images exist as ``v1.0``/``latest`` but
    not ``v1.1``. Trying a small fixed fallback list keeps evaluation
    compatible with both currently published image sets.
    """
    for tag in tags:
        if docker_manager.try_pull_prebuilt_image(instance_id, version=tag):
            if tag != tags[0]:
                logger.info(
                    "[%s] Pulled PolyBench image using fallback tag: %s",
                    instance_id,
                    tag,
                )
            return True
    return False


def evaluate_polybench_instance(
    patch: str,
    instance_info: dict[str, Any],
    timeout: int = 1800,
    result_path: str | None = None,
    delete_image: bool = False,
) -> dict[str, Any]:
    """Evaluate a single PolyBench instance using the official harness.

    Reconstructs a PolyBench evaluation flow:
      1. Pull or build the per-instance Docker image.
      2. Apply the test patch (ground-truth tests).
      3. Apply the code patch (model-generated fix).
      4. Run the test command.
      5. Parse test output with the repo-specific parser.
      6. Score against F2P / P2P expectations.

    Args:
        patch: Git diff format patch content (the model-generated fix).
        instance_info: Instance metadata dict from InstanceLoader.
            Must contain ``instance_id``, ``repo``, ``base_commit``,
            ``test_patch``, ``f2p``, ``p2p``, ``test_command``,
            ``language``, and optionally ``dockerfile``.
        timeout: Ignored (kept for API symmetry). PolyBench uses its own
            internal timeout via ``DEFAULT_TIMEOUT``.
        result_path: Directory for PolyBench result JSON files.
            When ``None``, a temporary directory is used.
        delete_image: Whether to delete the Docker image after evaluation.
            Defaults to False because image retention is governed by the
            configured Docker cache window.

    Returns:
        A dict compatible with the swebench evaluator's return shape:
        ``{"resolved": bool, "stdout": str, "stderr": str,
        "log_dir": str, "error_info": str | None, "report": dict}``.
    """
    pb = _import_polybench()
    DockerManager = pb["DockerManager"]
    PolyBenchInstance = pb["PolyBenchInstance"]
    instance_level_scoring = pb["instance_level_scoring"]
    store_instance_level_output = pb["store_instance_level_output"]
    DEFAULT_TIMEOUT = pb["DEFAULT_TIMEOUT"]
    REPO_TO_PARSER_CLASS = pb["REPO_TO_PARSER_CLASS"]

    instance_id = instance_info.get("instance_id", "")
    if not instance_id:
        raise FatalError("instance_info missing 'instance_id' field.")

    # Build a PolyBenchInstance from the normalized instance_info
    # (InstanceLoader has already mapped field names).
    try:
        inst = PolyBenchInstance(**instance_info)
    except Exception as exc:
        logger.error(
            "[%s] Failed to construct PolyBenchInstance: %s",
            instance_id,
            exc,
        )
        return {
            "resolved": False,
            "stdout": "",
            "stderr": f"PolyBenchInstance construction failed: {exc}",
            "log_dir": "",
            "error_info": f"PolyBenchInstance construction failed: {exc}",
            "report": {},
        }

    # Use a temporary result path if none provided
    if result_path is None:
        result_path = str(Path("/tmp") / "polybench_eval_results" / instance_id)
    Path(result_path).mkdir(parents=True, exist_ok=True)

    repo = inst.repo
    parser_class_name = REPO_TO_PARSER_CLASS.get(repo)
    if not parser_class_name:
        logger.error("[%s] Parser class not found for repo: %s", instance_id, repo)
        return {
            "resolved": False,
            "stdout": "",
            "stderr": f"Parser class not found for repo: {repo}",
            "log_dir": result_path,
            "error_info": f"Parser class not found for repo: {repo}",
            "report": {},
        }

    logger.info(
        "Running PolyBench evaluation: instance=%s repo=%s parser=%s",
        instance_id,
        repo,
        parser_class_name,
    )

    client = docker.from_env(timeout=720)
    image_id = f"polybench_{inst.language.lower()}_{instance_id.lower()}"
    docker_manager = DockerManager(
        image_id=image_id, delete_image=delete_image, client=client
    )

    # Track whether the patch was applied so we can return meaningful errors
    patch_applied = False
    generation = bool(patch and patch.strip())

    try:
        # ------------------------------------------------------------------
        # 1. Acquire Docker image (local / GHCR / build)
        # ------------------------------------------------------------------
        if docker_manager.check_image_local(local_image_name=image_id):
            logger.info("[%s] Using existing local image: %s", instance_id, image_id)
        elif _try_pull_prebuilt_image_with_fallback(docker_manager, instance_id):
            logger.info("[%s] Successfully pulled pre-built image from GHCR", instance_id)
        else:
            logger.warning(
                "[%s] Pre-built image not available locally or in GHCR. "
                "PolyBench evaluation requires the image to be pre-built "
                "or accessible. Consider building locally with the Dockerfile.",
                instance_id,
            )
            # Store a failure result
            output = instance_level_scoring(
                instance_id=instance_id,
                result={},
                f2p=inst.f2p,
                p2p=inst.p2p,
                patch_applied=False,
                generation=generation,
            )
            store_instance_level_output(
                instance_output=output, result_path=result_path
            )
            return {
                "resolved": False,
                "stdout": "",
                "stderr": (
                    f"Docker image unavailable for {instance_id}. "
                    f"Neither local image {image_id} nor GHCR pre-built image could be obtained."
                ),
                "log_dir": result_path,
                "error_info": "Docker image unavailable",
                "report": {},
            }

        # ------------------------------------------------------------------
        # 2. Create container and apply patches
        # ------------------------------------------------------------------
        docker_manager.create_container()

        # Apply test patch (ground-truth tests)
        try:
            docker_manager.apply_patch_to_container(
                patch_content=inst.test_patch, patch_type="test"
            )
        except Exception as exc:
            logger.warning(
                "[%s] Test patch apply error: %s",
                instance_id,
                exc,
            )
            output = instance_level_scoring(
                instance_id=instance_id,
                result={},
                f2p=inst.f2p,
                p2p=inst.p2p,
                patch_applied=False,
                generation=False,
            )
            store_instance_level_output(
                instance_output=output, result_path=result_path
            )
            docker_manager.__del__()
            return {
                "resolved": False,
                "stdout": "",
                "stderr": f"Test patch apply failed: {exc}",
                "log_dir": result_path,
                "error_info": f"Test patch apply failed: {exc}",
                "report": {},
            }

        # Apply code patch (model-generated fix)
        if not generation:
            # Empty patch — score as no-generation
            output = instance_level_scoring(
                instance_id=instance_id,
                result={},
                f2p=inst.f2p,
                p2p=inst.p2p,
                patch_applied=False,
                generation=False,
            )
            store_instance_level_output(
                instance_output=output, result_path=result_path
            )
            docker_manager.__del__()
            return {
                "resolved": False,
                "stdout": "",
                "stderr": "Empty patch",
                "log_dir": result_path,
                "error_info": "Empty patch",
                "report": {},
            }

        try:
            patch_success = docker_manager.apply_patch_to_container(
                patch_content=patch, patch_type="code"
            )
        except Exception as exc:
            patch_success = 1
            logger.warning(
                "[%s] Code patch apply error: %s",
                instance_id,
                exc,
            )

        if patch_success != 0:
            output = instance_level_scoring(
                instance_id=instance_id,
                result={},
                f2p=inst.f2p,
                p2p=inst.p2p,
                patch_applied=False,
                generation=True,
            )
            store_instance_level_output(
                instance_output=output, result_path=result_path
            )
            docker_manager.__del__()
            return {
                "resolved": False,
                "stdout": "",
                "stderr": "Code patch apply failed",
                "log_dir": result_path,
                "error_info": "Code patch apply failed",
                "report": {},
            }

        patch_applied = True

        # ------------------------------------------------------------------
        # 3. Run tests
        # ------------------------------------------------------------------
        run_timeout = DEFAULT_TIMEOUT
        _ = docker_manager.docker_run(
            test_command=inst.test_command, timeout=run_timeout
        )

        run_logs_string = "\n".join(docker_manager.run_logs)

        # ------------------------------------------------------------------
        # 4. Parse test output
        # ------------------------------------------------------------------
        all_parsers = importlib.import_module("poly_bench_evaluation.parsers")
        if hasattr(all_parsers, parser_class_name):
            parser_class = getattr(all_parsers, parser_class_name)
            log_parser = parser_class(test_content=run_logs_string)
            result = log_parser.parse()
        else:
            raise FatalError(
                f"Parser class {parser_class_name} not found in parsers module"
            )

        # ------------------------------------------------------------------
        # 5. Score
        # ------------------------------------------------------------------
        output = instance_level_scoring(
            instance_id=instance_id,
            result=result,
            f2p=inst.f2p,
            p2p=inst.p2p,
            patch_applied=patch_applied,
            generation=generation,
        )
        store_instance_level_output(
            instance_output=output, result_path=result_path
        )

        # Read back the stored result for the return dict
        result_file = Path(result_path) / f"{instance_id}_result.json"
        report_data: dict[str, Any] = {}
        if result_file.exists():
            try:
                report_data = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        resolved = getattr(output, "resolved", False)

        return {
            "resolved": resolved,
            "stdout": run_logs_string[:4000] if run_logs_string else "",
            "stderr": "",
            "log_dir": result_path,
            "error_info": None,
            "report": {instance_id: report_data},
        }

    except FatalError:
        raise
    except Exception as exc:
        if is_docker_storage_error(str(exc)):
            raise FatalError(
                "Docker storage error during PolyBench evaluation. "
                "Stop the batch and free Docker disk space before retrying. "
                f"Instance={instance_id}. Error: {exc}"
            ) from exc
        logger.error(
            "[%s] PolyBench evaluation failed: %s",
            instance_id,
            exc,
        )
        return {
            "resolved": False,
            "stdout": "",
            "stderr": f"PolyBench evaluation error: {exc}",
            "log_dir": result_path,
            "error_info": f"{type(exc).__name__}: {exc}",
            "report": {},
        }
    finally:
        try:
            docker_manager.__del__()
        except Exception:
            pass
