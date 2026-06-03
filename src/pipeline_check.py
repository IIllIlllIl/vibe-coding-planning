"""Plan-Check-Code pipeline (single pass, no reflection).

Orchestrates plan generation, plan checking against rules, code generation,
and evaluation in a single round. The check result is recorded for metric
calculation but does not prevent code execution — code always runs to
produce ground-truth resolution data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents import check_agent, code_agent, plan_agent
from src.config import Config
from src.data.instance_loader import InstanceLoader
from src.environment.docker_env import DockerEnvWrapper, cleanup_docker_image_cache
from src.evaluator.swe_evaluator import derive_image_name, evaluate
from src.exceptions import FatalError, TaskError
from src.output.trajectory import save_trajectory
from src.output.writer import OutputWriter
from src.rules.rule_loader import format_rules_for_prompt, load_aggregated_rules

logger = logging.getLogger(__name__)


def _dataset_short(dataset: str) -> str:
    """Derive a filesystem-friendly short name from a HuggingFace dataset ID."""
    if not dataset:
        return "default"
    return dataset.rsplit("/", 1)[-1]


def run_instance(instance_id: str, config: Config) -> dict[str, Any]:
    """Run the Plan-Check-Code pipeline for a single instance.

    Args:
        instance_id: SWE-bench instance identifier.
        config: Full configuration object.

    Returns:
        The result dict with check_result and test_results included.
    """
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    output_dir = str(
        Path(config.system.output_dir)
        / _dataset_short(config.system.dataset)
        / config.system.batch_id
        / instance_id
    )
    writer = OutputWriter(output_dir, run_id)

    try:
        return _run_instance_core(instance_id, config, writer)
    except FatalError:
        writer.emergency_save()
        raise


def _run_instance_core(
    instance_id: str,
    config: Config,
    writer: OutputWriter,
) -> dict[str, Any]:
    """Inner pipeline without the outer fatal-error wrapper."""

    # ------------------------------------------------------------------
    # 1. Load instance metadata
    # ------------------------------------------------------------------
    loader = InstanceLoader(
        dataset=config.system.dataset,
        dataset_type=config.system.dataset_type,
        language_filter=config.system.language_filter,
    )
    try:
        instance_info = loader.load_instance(instance_id)
    except TaskError as exc:
        logger.error("Failed to load instance %s: %s", instance_id, exc)
        writer.record_error(
            instance_id=instance_id,
            error_type="instance_load_failed",
            message=str(exc),
            skipped=True,
        )
        return _finalize_writer(writer, config, instance_id)

    issue_description = instance_info.get("problem_statement", "")
    if not issue_description:
        issue_description = instance_info.get("issue_description", "")

    # ------------------------------------------------------------------
    # 2. Load rules (if checker enabled)
    # ------------------------------------------------------------------
    rules_text = ""
    if config.checker.enabled:
        try:
            rules = load_aggregated_rules(config.checker.rules_path)
            rules_text = format_rules_for_prompt(rules)
            logger.info(
                "[%s] Loaded %d always rules + %d branches",
                instance_id,
                len(rules.get("always", [])),
                len(rules.get("branches", [])),
            )
        except Exception as exc:
            logger.warning("[%s] Failed to load rules: %s", instance_id, exc)
            # Continue without rules; check agent will have empty rules text

    # ------------------------------------------------------------------
    # 3. Prepare Docker wrapper
    # ------------------------------------------------------------------
    docker = DockerEnvWrapper(config.docker)
    image_name = derive_image_name(instance_info)
    repo_path = instance_info.get("repo_path", "")

    # Pro instances use /app as workdir; Verified and PolyBench use config value
    dataset_type = instance_info.get("dataset_type", "")
    is_pro = dataset_type == "pro" or "dockerhub_tag" in instance_info
    workdir = "/app" if is_pro else config.docker.workdir

    # ------------------------------------------------------------------
    # 4. Run single round: Plan → Check → Code → Evaluate
    # ------------------------------------------------------------------
    logger.info("[%s] === Plan-Check-Code pipeline ===", instance_id)

    try:
        docker.start(
            image=image_name,
            workdir=workdir,
            mount_source=repo_path,
            timeout=config.agent.timeout,
        )
    except FatalError:
        raise
    except Exception as exc:
        logger.error("[%s] Docker start failed: %s", instance_id, exc)
        writer.record_error(
            instance_id=instance_id,
            error_type="docker_start_failed",
            message=str(exc),
            skipped=True,
        )
        return _finalize_writer(writer, config, instance_id)

    plan_text = ""
    traj_plan: list[dict[str, Any]] = []
    check_result: dict[str, Any] = {}
    patch_text = ""
    traj_code: list[dict[str, Any]] = []
    test_results: dict[str, Any] = {}

    try:
        # ---- Plan ----
        logger.info("[%s] Generating plan", instance_id)
        plan_text, traj_plan = plan_agent.run(config, issue_description, docker)
        plan_id = f"plan_{instance_id}_check"

        # Save plan trajectory
        trajectories_dir = str(Path(writer.output_dir) / "trajectories")
        traj_plan_path = str(
            save_trajectory(
                traj_plan,
                round_num=1,
                role="plan_gen",
                output_dir=trajectories_dir,
            )
        )

        # Save plan text
        plan_path_obj = writer.save_plan(
            round_num=1,
            role="plan_gen",
            plan_content=plan_text,
        )
        plan_path = str(plan_path_obj.relative_to(writer.output_dir))

        # ---- Check ----
        if config.checker.enabled:
            logger.info("[%s] Checking plan against rules", instance_id)
            try:
                check_result, traj_check = check_agent.run(
                    config, plan_text, issue_description, rules_text, docker
                )

                # Save check trajectory
                save_trajectory(
                    traj_check,
                    round_num=1,
                    role="reflect",  # reusing reflect role for check trajectory
                    output_dir=trajectories_dir,
                )

                # Save check result
                check_result_path = Path(writer.output_dir) / "check_result.json"
                check_result_path.write_text(
                    json.dumps(check_result, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                logger.info(
                    "[%s] Check result: passed=%s violations=%d",
                    instance_id,
                    check_result.get("passed"),
                    len(check_result.get("violations", [])),
                )
            except TaskError as exc:
                logger.warning("[%s] Check agent failed: %s", instance_id, exc)
                check_result = {
                    "passed": False,
                    "violations": [
                        {
                            "rule": "Check agent failed",
                            "reasoning": str(exc),
                        }
                    ],
                    "overall_assessment": f"Check agent error: {exc}",
                    "_check_error": True,
                }
        else:
            check_result = {
                "passed": True,
                "violations": [],
                "overall_assessment": "Checker disabled",
            }

        # ---- Code ----
        logger.info("[%s] Generating code", instance_id)
        patch_text, traj_code = code_agent.run(
            config, plan_text, issue_description, docker
        )

        # Save code trajectory
        save_trajectory(
            traj_code,
            round_num=1,
            role="code_gen",
            output_dir=trajectories_dir,
        )

        # ---- Evaluate ----
        logger.info("[%s] Evaluating patch", instance_id)
        test_results = evaluate(
            patch_text,
            instance_info,
            timeout=config.evaluator.timeout,
            run_id_suffix="_check",
        )

        # ---- Save round output ----
        writer.save_round(
            round_num=1,
            plan_id=plan_id,
            generated_by="plan_agent",
            plan_content=plan_text,
            patch_content=patch_text,
            test_results=test_results,
            trajectory_path=traj_plan_path,
            plan_path=plan_path,
            reflection_log=None,
            optimized_from=None,
        )

    except TaskError as exc:
        logger.warning("[%s] Pipeline step failed: %s", instance_id, exc)
        writer.record_error(
            instance_id=instance_id,
            error_type="pipeline_step_failed",
            message=str(exc),
            skipped=True,
        )
    except FatalError:
        raise
    finally:
        logger.info("[%s] Stopping Docker env", instance_id)
        docker.stop()
        if config.docker.delete_images_after_instance:
            cleanup_docker_image_cache(config.docker.max_cached_images)

    # ------------------------------------------------------------------
    # 5. Finalize output with check result
    # ------------------------------------------------------------------
    result = _finalize_writer(writer, config, instance_id)

    # Augment result with check_result and test_results for caller convenience
    result["check_result"] = check_result
    result["test_results"] = test_results
    result["resolved"] = test_results.get("resolved", False)

    return result


def _finalize_writer(
    writer: OutputWriter,
    config: Config,
    instance_id: str,
) -> dict[str, Any]:
    """Finalize the writer and return the result dict."""
    runtime_versions = _collect_runtime_versions()
    result_path = writer.finalize(
        instances=[instance_id],
        dataset=config.system.dataset,
        model=config.system.model,
        parameter_n=1,
        optimization_info_level=config.system.optimization_info_level,
        runtime_versions=runtime_versions,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _collect_runtime_versions() -> dict[str, str]:
    """Collect version info for reproducibility."""
    versions: dict[str, str] = {}
    try:
        import minisweagent

        versions["mini_swe_agent"] = getattr(minisweagent, "__version__", "unknown")
    except Exception:
        versions["mini_swe_agent"] = "unknown"
    try:
        import swebench

        versions["swebench"] = getattr(swebench, "__version__", "unknown")
    except Exception:
        versions["swebench"] = "unknown"
    try:
        from importlib.metadata import version

        versions["litellm"] = version("litellm")
    except Exception:
        versions["litellm"] = "unknown"
    return versions
