"""Reflection agent.

Runs inside a Docker container using DefaultAgent's interactive step
loop, similar to plan_agent. The agent may read files and write
temporary scripts, but must not modify source under ``/testbed``.

The reflection system prompt is rendered on the host by combining:

* the current plan (previous round's output);
* a level-aware feedback intro (which fields are present this round);
* the assembled feedback body (trajectories + optional test results +
  patch); and
* the shared NRPV block (single source for the four-section plan
  structure).

All four pieces are pre-baked into ``system_template`` before the agent
starts; the agent itself has no access to trajectory files in the
container. The issue description is delivered separately via the
configured ``reflect_instance_template`` (Jinja-rendered with the
``task`` kwarg by DefaultAgent), so the reflection agent sees the same
``<pr_description>`` wrapper as the plan and code agents.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
)
from src.config import Config
from src.exceptions import TaskError
from src.prompts import gepa_reflection

logger = logging.getLogger(__name__)


def _read_plan_from_file(env: Any) -> str | None:
    """Try to read /tmp/plan.md from the Docker container.

    Returns:
        The file content if successfully read and non-empty, otherwise None.
    """
    try:
        result = env.execute("cat /tmp/plan.md")
        if result.get("returncode") == 0:
            content = result.get("output", "").strip()
            if content:
                return content
    except Exception:
        pass
    return None


def run(
    config: Config,
    current_plan: str,
    feedback_intro: str,
    feedback_body: str,
    issue_description: str,
    env: Any,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the reflection agent to generate an improved Plan.

    Args:
        config: Full configuration object.
        current_plan: The plan being optimised (previous round's plan).
        feedback_intro: Level-aware paragraph naming the feedback fields
            present this round (rendered host-side; varies with
            ``config.system.optimization_info_level``).
        feedback_body: Assembled execution context (trajectories + optional
            test results + patch) built by the pipeline on the host.
        issue_description: Original SWE-bench issue text. Forwarded to
            DefaultAgent as ``task`` so the reflection agent sees the same
            ``<pr_description>`` wrapper as the plan and code agents.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).

    Returns:
        A tuple of ``(new_plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces empty or too-short output, or
            terminates without a submission.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    system_template = gepa_reflection.render(
        current_plan=current_plan,
        feedback_intro=feedback_intro,
        feedback_body=feedback_body,
        nrpv_block=config.prompts.nrpv_block,
        template=config.prompts.reflection_prompt_template,
    )
    instance_template = config.prompts.reflect_instance_template or None

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.api_key,
        api_base=config.system.api_base,
    )

    agent = build_default_agent(
        DefaultAgent,
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
        instance_template=instance_template,
    )

    logger.info(
        "Starting reflect agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    exception_name, exception_msg = agent.run(task=issue_description)

    # Try to read plan from the file the agent wrote in the container
    plan_text = _read_plan_from_file(env)
    if plan_text is None:
        if exception_name == "Submitted":
            plan_text = exception_msg.strip()
            # Extract the plan from ```-fenced block (template instructs the
            # LLM to output inside fences) only when reading from messages;
            # file content is already plain text
            if plan_text:
                plan_text = gepa_reflection.parse_output(plan_text)
        else:
            raise TaskError(
                f"Reflect agent terminated without a submission (exit_status={exception_name}). "
                f"Expected the agent to write an improved plan to /tmp/plan.md and finish with: "
                f"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            )

    if not plan_text or not plan_text.strip():
        raise TaskError(
            "Reflect agent submitted but /tmp/plan.md was empty and no plan text was returned."
        )

    plan_text = plan_text.strip()
    if len(plan_text) < 50:
        raise TaskError(
            f"Reflect agent output too short ({len(plan_text)} chars). "
            "Expected a detailed plan."
        )

    return plan_text, agent.messages
