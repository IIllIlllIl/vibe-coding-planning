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
from src.environment.docker_env import DockerEnvWrapper, cleanup_docker_image_cache
from src.evaluator.swe_evaluator import derive_image_name, evaluate
from src.exceptions import FatalError, TaskError
from src.output.trajectory import save_trajectory
from src.output.writer import OutputWriter

logger = logging.getLogger(__name__)


def _dataset_short(dataset: str) -> str:
    """Derive a filesystem-friendly short name from a HuggingFace dataset ID.

    ``SWE-bench/SWE-bench_Verified`` → ``SWE-bench_Verified``.
    Falls back to ``"default"`` for empty input. The full dataset name
    is always preserved verbatim in ``result.json["dataset"]`` — this
    helper exists only to keep output directory names readable.
    """
    if not dataset:
        return "default"
    # HuggingFace dataset names are namespaced as "<owner>/<name>".
    # We only need the trailing component for the directory layout.
    return dataset.rsplit("/", 1)[-1]


def run_instance(instance_id: str, config: Config) -> dict[str, Any]:
    """Run the full plan-code-test pipeline for a single instance.

    Args:
        instance_id: SWE-bench instance identifier.
        config: Full configuration object.

    Returns:
        The result dict (same structure as writer.finalize output).
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
        # Fallback: some loaders may use different field names
        issue_description = instance_info.get("issue_description", "")

    # ------------------------------------------------------------------
    # 2. Prepare Docker wrapper (container is started per-round below)
    # ------------------------------------------------------------------
    docker = DockerEnvWrapper(config.docker)
    image_name = derive_image_name(instance_info)
    repo_path = instance_info.get("repo_path", "")

    # ------------------------------------------------------------------
    # 3. Run rounds — each round gets a fresh container so every agent
    #    starts with /testbed at the dataset's base_commit state.
    # ------------------------------------------------------------------
    previous_plan: str | None = None
    previous_plan_id: str | None = None
    previous_patch: str = ""
    previous_test_results: dict[str, Any] = {}

    for round_num in range(1, config.system.n + 1):
        logger.info("[%s] === Round %d/%d ===", instance_id, round_num, config.system.n)

        try:
            logger.info(
                "[%s] Round %d: starting Docker env: image=%s",
                instance_id,
                round_num,
                image_name,
            )
            try:
                docker.start(
                    image=image_name,
                    workdir=config.docker.workdir,
                    mount_source=repo_path,
                    timeout=config.agent.timeout,
                    instance_info=instance_info,
                )
            except FatalError:
                raise
            except Exception as exc:
                logger.error("[%s] Round %d: docker start failed: %s", instance_id, round_num, exc)
                writer.record_error(
                    instance_id=instance_id,
                    error_type="docker_start_failed",
                    message=f"Round {round_num}: {exc}",
                    skipped=True,
                )
                break

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
            finally:
                logger.info("[%s] Round %d: stopping Docker env", instance_id, round_num)
                docker.stop()
        except TaskError as exc:
            logger.warning("[%s] Round %d failed: %s", instance_id, round_num, exc)
            writer.record_error(
                instance_id=instance_id,
                error_type="round_failed",
                message=f"Round {round_num}: {exc}",
                skipped=True,
            )
            # Instance-level skip: do not attempt remaining rounds for this instance
            break
        except FatalError:
            # Re-raise to be caught by outer wrapper
            raise

        # Save state for next round
        previous_plan = plan_text
        previous_plan_id = plan_id
        previous_patch = patch_text
        previous_test_results = test_results

        # Optional early exit: when skip_completed_rounds is true and the
        # instance was resolved this round, stop iterating. The resolved
        # round's outputs are already persisted by save_round above.
        # Default behaviour (skip_completed_rounds=false) runs all n
        # rounds regardless of resolved status.
        if config.system.skip_completed_rounds and test_results.get("resolved"):
            if round_num < config.system.n:
                logger.info(
                    "[%s] Round %d resolved; skipping remaining %d round(s) "
                    "(skip_completed_rounds=true).",
                    instance_id,
                    round_num,
                    config.system.n - round_num,
                )
            break

    # ------------------------------------------------------------------
    # 4. Finalize output
    # ------------------------------------------------------------------
    if config.docker.delete_images_after_instance:
        cleanup_docker_image_cache(config.docker.max_cached_images)

    return _finalize_writer(writer, config, instance_id)


def _find_latest_trajectory(trajectories_dir: str, round_num: int, role: str) -> str | None:
    """Find the most recent trajectory file for a given round and role.

    Trajectory filenames follow the convention
    ``trajectory_{round_num}_{role}_{timestamp}.json`` written by
    :func:`save_trajectory`.
    """
    dir_path = Path(trajectories_dir)
    if not dir_path.exists():
        return None
    pattern = f"trajectory_{round_num}_{role}_*.json"
    files = sorted(dir_path.glob(pattern))
    return str(files[-1]) if files else None


def _read_trajectory_content(path: str | None) -> str:
    """Read a trajectory JSON file and return formatted message text.

    Each message is rendered as ``[{role}]:\n{content}`` so the reflect
    agent can follow the reasoning flow.  Returns empty string if the
    path is None or the file does not exist.
    """
    if not path:
        return ""
    filepath = Path(path)
    if not filepath.exists():
        return ""
    try:
        import json

        data = json.loads(filepath.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
        if not messages:
            return ""
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                parts.append(f"[{role}]:\n{content}\n")
        return "\n".join(parts)
    except Exception:
        return ""


def _build_feedback_text(
    *,
    plan_trajectory: str,
    code_trajectory: str,
    reflect_trajectory: str,
    test_results: dict[str, Any],
    patch: str,
    opt_level: int,
) -> tuple[str, str]:
    """Assemble the level-aware intro and feedback body for the reflect prompt.

    All content is gathered on the host so the reflect agent inside Docker
    never needs to read trajectory files.  The current plan and the
    original issue text are NOT part of either return value — the plan is
    forwarded to :func:`src.agents.reflect_agent.run` as ``current_plan``
    (which mini-swe-agent fills into the ``{{prompt_template}}`` Jinja
    placeholder at run() time), and the issue is delivered through the
    reflect agent's instance_template (also Jinja-rendered with ``task``).

    The returned tuple is ``(feedback_intro, feedback_body)``:

    * ``feedback_intro`` — a short paragraph naming the feedback fields
      present this round; varies with ``opt_level`` because not every
      level supplies test results.
    * ``feedback_body`` — the assembled trajectories, optional test
      results, and the patch, in causal order
      (planner-or-reflector → coder → outcome).

    For round 2 only the plan-agent trajectory is available; for round
    3+ only the reflect-agent trajectory is available. They are mutually
    exclusive — the assert guards against an upstream regression that
    might supply both.
    """
    assert not (plan_trajectory and reflect_trajectory), (
        "plan_trajectory and reflect_trajectory are mutually exclusive: "
        "round 2 has plan_trajectory only, round 3+ has reflect_trajectory only"
    )

    # ----- intro: list of fields the reflector will see this round -----
    intro_lines = [
        "The following is the execution context from the most recent round. You will see:",
        "- The trajectory of the planning agent (round 2) or reflection agent (round 3+) that produced the plan above",
        "- The trajectory of the code agent that executed the plan",
    ]
    if opt_level >= 1:
        intro_lines.append("- Test results from running the generated patch")
    intro_lines.append("- The patch the code agent generated")
    feedback_intro = "\n".join(intro_lines)

    # ----- body: trajectories first, then outcome ---------------------
    parts: list[str] = []

    # Causal order: planner/reflector produced the plan, then coder
    # executed it. Place the producing-agent trajectory before the
    # executing-agent trajectory so the reflector reads them in order.
    if plan_trajectory:
        parts.append(f"=== Plan Agent Trajectory ===\n{plan_trajectory}")
    elif reflect_trajectory:
        parts.append(f"=== Reflect Agent Trajectory ===\n{reflect_trajectory}")
    if code_trajectory:
        parts.append(f"=== Code Agent Trajectory ===\n{code_trajectory}")

    if opt_level >= 1:
        resolved = test_results.get("resolved")
        stdout = test_results.get("stdout", "")
        stderr = test_results.get("stderr", "")
        if resolved is not None or stdout or stderr:
            test_lines = ["=== Test Results ==="]
            if resolved is not None:
                test_lines.append(f"Resolved: {resolved}")
            if stdout:
                test_lines.append(f"STDOUT:\n{stdout}")
            if stderr:
                test_lines.append(f"STDERR:\n{stderr}")
            parts.append("\n".join(test_lines))

    if patch:
        parts.append(f"=== Generated Patch ===\n{patch}")

    feedback_body = "\n\n".join(parts)
    return feedback_intro, feedback_body


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
    # Use the writer's output_dir as the single source of truth for where
    # trajectories/patches/plans live; otherwise we'd re-derive the path
    # and risk drifting from the writer's view (e.g. dataset stratification).
    output_dir = str(writer.output_dir)

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
        trajectories_dir = str(Path(output_dir) / "trajectories")

        # Locate trajectory files from previous rounds for GEPA analysis.
        # Round 2: plan agent (r1) + code agent (r1)
        # Round 3+: previous reflect agent (rN-1) + code agent (rN-1)
        if round_num == 2:
            plan_traj_path = _find_latest_trajectory(trajectories_dir, 1, "plan_gen")
            reflect_traj_path = None
        else:
            plan_traj_path = None
            reflect_traj_path = _find_latest_trajectory(trajectories_dir, round_num - 1, "reflect")
        code_traj_path = _find_latest_trajectory(trajectories_dir, round_num - 1, "code_gen")

        # Read trajectory contents on the host — the reflect agent inside
        # Docker will never see these files.
        plan_traj = _read_trajectory_content(plan_traj_path)
        code_traj = _read_trajectory_content(code_traj_path)
        reflect_traj = _read_trajectory_content(reflect_traj_path)

        feedback_intro, feedback_body = _build_feedback_text(
            plan_trajectory=plan_traj,
            code_trajectory=code_traj,
            reflect_trajectory=reflect_traj,
            test_results=previous_test_results,
            patch=previous_patch,
            opt_level=config.system.optimization_info_level,
        )

        plan_text, traj_plan = reflect_agent.run(
            config,
            previous_plan or "",
            feedback_intro,
            feedback_body,
            issue_description,
            docker,
        )
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

    # Save plan text to plans/ directory
    plan_role = "plan_gen" if round_num == 1 else "reflect"
    plan_path_obj = writer.save_plan(
        round_num=round_num,
        role=plan_role,
        plan_content=plan_text,
    )
    plan_path = str(plan_path_obj.relative_to(writer.output_dir))

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

    patch_policy: dict[str, Any] | None = None
    if instance_info.get("dataset_type") == "polybench":
        from src.evaluator.polybench_patch_policy import apply_polybench_patch_policy

        policy_result = apply_polybench_patch_policy(
            patch_text,
            test_patch=str(instance_info.get("test_patch", "")),
        )
        patch_text = policy_result.patch
        patch_policy = {
            "kept_files": list(policy_result.kept_files),
            "removed_files": list(policy_result.removed_files),
            "test_overlap_files": list(policy_result.test_overlap_files),
        }
        if policy_result.removed_files:
            logger.warning(
                "[%s] Round %d: removed forbidden patch files: %s",
                instance_id,
                round_num,
                ", ".join(policy_result.removed_files),
            )

    patch_path_obj = writer.save_patch(
        round_num=round_num,
        patch_content=patch_text,
    )
    patch_path = str(patch_path_obj.relative_to(writer.output_dir))

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------
    logger.info("[%s] Round %d: evaluating", instance_id, round_num)
    test_results = evaluate(
        patch_text,
        instance_info,
        timeout=config.evaluator.timeout,
        run_id_suffix=f"_r{round_num}",
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
        plan_path=plan_path,
        reflection_log=None,
        optimized_from=previous_plan_id if round_num > 1 else None,
        patch_path=patch_path,
    )
    if patch_policy is not None:
        writer.plans[-1]["patch_policy"] = patch_policy

    return plan_text, traj_plan, plan_id, patch_text, test_results


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
        parameter_n=config.system.n,
        optimization_info_level=config.system.optimization_info_level,
        runtime_versions=runtime_versions,
    )
    # Load and return the written result
    return json.loads(result_path.read_text(encoding="utf-8"))
