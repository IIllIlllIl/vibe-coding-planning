"""Core pipeline: single-instance plan-code-test loop.

Orchestrates plan generation, code generation, evaluation, and reflection
over n rounds.  Handles per-round errors without crashing the entire pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agents import code_agent, plan_agent, reflect_agent
from src.config import Config
from src.data.instance_loader import InstanceLoader
from src.environment.docker_env import DockerEnvWrapper
from src.evaluator.swe_evaluator import derive_image_name, evaluate
from src.exceptions import FatalError, TaskError
from src.feedback import assembler as feedback_assembler
from src.output.trajectory import save_trajectory
from src.output.writer import OutputWriter

logger = logging.getLogger(__name__)


def _iso_timestamp() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def run_instance(instance_id: str, config: Config) -> dict[str, Any]:
    """Run the full plan-code-test pipeline for a single instance.

    Args:
        instance_id: SWE-bench Pro instance identifier.
        config: Full configuration object.

    Returns:
        The result dict (same structure as writer.finalize output).
    """
    run_id = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    output_dir = str(Path(config.system.output_dir) / instance_id)
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
    loader = InstanceLoader()
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
        # Fallback: some loaders may use different field names
        issue_description = instance_info.get("issue_description", "")

    # ------------------------------------------------------------------
    # 2. Start Docker environment
    # ------------------------------------------------------------------
    docker = DockerEnvWrapper(config.docker)
    image_name = derive_image_name(instance_info)
    repo_path = instance_info.get("repo_path", "")

    logger.info("[%s] Starting Docker env: image=%s", instance_id, image_name)
    try:
        docker.start(
            image=image_name,
            workdir=config.docker.workdir,
            ro_mount_source=repo_path,
        )
    except FatalError:
        raise
    except Exception as exc:
        logger.error("Docker start failed: %s", exc)
        writer.record_error(
            instance_id=instance_id,
            error_type="docker_start_failed",
            message=str(exc),
            skipped=True,
        )
        return _finalize_writer(writer, config, instance_id)

    # ------------------------------------------------------------------
    # 3. Run rounds
    # ------------------------------------------------------------------
    previous_plan: str | None = None
    previous_plan_id: str | None = None
    previous_patch: str = ""
    previous_test_results: dict[str, Any] = {}

    for round_num in range(1, config.system.n + 1):
        logger.info("[%s] === Round %d/%d ===", instance_id, round_num, config.system.n)

        try:
            (
                plan_text,
                traj_plan,
                plan_id,
                patch_text,
                test_results,
            ) = _run_round(
                round_num=round_num,
                config=config,
                docker=docker,
                writer=writer,
                instance_id=instance_id,
                instance_info=instance_info,
                issue_description=issue_description,
                previous_plan=previous_plan,
                previous_plan_id=previous_plan_id,
                previous_patch=previous_patch,
                previous_test_results=previous_test_results,
            )
        except TaskError as exc:
            logger.warning("[%s] Round %d failed: %s", instance_id, round_num, exc)
            writer.record_error(
                instance_id=instance_id,
                error_type="round_failed",
                message=f"Round {round_num}: {exc}",
                skipped=False,
            )
            # Continue to next round if possible
            continue
        except FatalError:
            # Re-raise to be caught by outer wrapper
            raise

        # Save state for next round
        previous_plan = plan_text
        previous_plan_id = plan_id
        previous_patch = patch_text
        previous_test_results = test_results

    # ------------------------------------------------------------------
    # 4. Stop Docker environment
    # ------------------------------------------------------------------
    logger.info("[%s] Stopping Docker env", instance_id)
    docker.stop()

    # ------------------------------------------------------------------
    # 5. Finalize output
    # ------------------------------------------------------------------
    return _finalize_writer(writer, config, instance_id)


def _run_round(
    *,
    round_num: int,
    config: Config,
    docker: DockerEnvWrapper,
    writer: OutputWriter,
    instance_id: str,
    instance_info: dict[str, Any],
    issue_description: str,
    previous_plan: str | None,
    previous_plan_id: str | None,
    previous_patch: str,
    previous_test_results: dict[str, Any],
) -> tuple[str, list[dict], str, str, dict[str, Any]]:
    """Execute a single round and return the plan + trajectory + patch + results.

    For round 1, uses plan_agent.  For rounds >= 2, uses reflect_agent.
    """
    output_dir = str(Path(config.system.output_dir) / instance_id)

    # ------------------------------------------------------------------
    # Generate Plan
    # ------------------------------------------------------------------
    if round_num == 1:
        logger.info("[%s] Round %d: generating plan", instance_id, round_num)
        plan_text, traj_plan = plan_agent.run(config, issue_description, docker)
        plan_id = f"plan_{instance_id}_r1"
        generated_by = "plan_agent"
    else:
        logger.info("[%s] Round %d: reflecting on previous plan", instance_id, round_num)
        feedback_data = feedback_assembler.assemble(
            feedback_assembler.FeedbackInput(
                optimization_info_level=config.system.optimization_info_level,
                target_plan_number=config.system.n,
                current_round=round_num,
                model=config.system.model,
                use_gepa_reflection_prompt=config.system.use_gepa_reflection_prompt,
                original_prompt=issue_description,
                current_plan_content=previous_plan or "",
                current_plan_id=previous_plan_id or "",
                current_plan_round=round_num - 1,
                plan_generation_trajectory_path=None,
                code_generation_trajectory_path=None,
                reflection_trajectory_path=None,
                patch_path="",
                patch_content=previous_patch,
                test_resolved=previous_test_results.get("resolved", False),
                test_stdout=previous_test_results.get("stdout", ""),
                test_stderr=previous_test_results.get("stderr", ""),
                test_log_dir=previous_test_results.get("log_dir", ""),
                error_info="",
            )
        )
        feedback_data["meta"]["timestamp"] = _iso_timestamp()

        plan_text, traj_plan = reflect_agent.run(config, feedback_data)
        plan_id = f"plan_{instance_id}_r{round_num}"
        generated_by = "reflect_agent"

    # Save plan trajectory
    trajectories_dir = str(Path(output_dir) / "trajectories")
    traj_plan_path = str(
        save_trajectory(
            traj_plan,
            round_num=round_num,
            role="plan_gen" if round_num == 1 else "reflect",
            output_dir=trajectories_dir,
        )
    )

    # ------------------------------------------------------------------
    # Generate Code
    # ------------------------------------------------------------------
    logger.info("[%s] Round %d: generating code", instance_id, round_num)
    patch_text, traj_code = code_agent.run(
        config, plan_text, issue_description, docker
    )

    # Save code trajectory
    save_trajectory(
        traj_code,
        round_num=round_num,
        role="code_gen",
        output_dir=trajectories_dir,
    )

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------
    logger.info("[%s] Round %d: evaluating", instance_id, round_num)
    test_results = evaluate(
        patch_text,
        instance_info,
        timeout=config.evaluator.timeout,
    )

    # ------------------------------------------------------------------
    # Save round output
    # ------------------------------------------------------------------
    writer.save_round(
        round_num=round_num,
        plan_id=plan_id,
        generated_by=generated_by,
        plan_content=plan_text,
        patch_content=patch_text,
        test_results=test_results,
        trajectory_path=traj_plan_path,
        reflection_log=None,
        optimized_from=previous_plan_id if round_num > 1 else None,
    )

    return plan_text, traj_plan, plan_id, patch_text, test_results


def _finalize_writer(
    writer: OutputWriter,
    config: Config,
    instance_id: str,
) -> dict[str, Any]:
    """Finalize the writer and return the result dict."""
    result_path = writer.finalize(
        swe_pro_instances=[instance_id],
        model=config.system.model,
        parameter_n=config.system.n,
        optimization_info_level=config.system.optimization_info_level,
        use_gepa_reflection_prompt=config.system.use_gepa_reflection_prompt,
    )
    # Load and return the written result
    return json.loads(result_path.read_text(encoding="utf-8"))
