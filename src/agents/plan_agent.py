"""Plan generation agent.

Uses DefaultAgent's interactive step loop so the agent can explore the
codebase (via cat, grep, ls, etc.) before producing the structured Plan.
Safe-PCE prompts terminate with a direct ``FINAL_PLAN`` response intercepted
before the shell action parser. Legacy prompts may still submit through the
ordinary mini-swe stdout marker and retain their historical phase-local-file
preference for frozen-run compatibility.
"""

from __future__ import annotations

import logging
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.config import Config
from src.exceptions import AgentTaskError, CommandTimeoutError

logger = logging.getLogger(__name__)

DIRECT_PLAN_MARKER = "FINAL_PLAN"
_DIRECT_PLAN_PAYLOAD_PREFIX = "__VIBE_DIRECT_PLAN_V1__\n"
_DIRECT_PLAN_HEADINGS = (
    "# Plan",
    "## Navigation (N)",
    "## Reproduction (R)",
    "## Patch (P)",
    "## Validation (V)",
)
_PROTOCOL_RESIDUE = (
    "</parameter>",
    "<berkeleyos>",
    "</berkeleyos>",
    "<result>",
    "</result>",
    "<｜｜DSML｜｜",
    "</｜｜DSML｜｜",
)

PLAN_ACTION_PROTOCOL = """\
## Planner action and final-submission protocol

During repository exploration, return exactly one executable bash block per
response. The parser executes the shell body captured from that block. Wait for
its real observation before choosing the next action.

When the Plan is complete, do not execute another command and do not write the
Plan to a file. Return a terminal response whose first non-whitespace text is
exactly `FINAL_PLAN` on its own line. Put the complete Plan directly after that
line. A terminal response contains no bash action block. The Host intercepts it
before shell parsing and preserves the remaining text verbatim.
"""


def _direct_plan_agent_class(default_agent: type, submitted: type) -> type:
    """Add a Plan-only terminal response without changing mini-swe-agent."""

    class DirectPlanAgent(default_agent):
        def get_observation(self, response: dict[str, Any]) -> dict[str, Any]:
            content = str(response.get("content", ""))
            candidate = content.lstrip()
            marker = DIRECT_PLAN_MARKER + "\n"
            if candidate.startswith(marker):
                plan = candidate[len(marker) :]
                raise submitted(_DIRECT_PLAN_PAYLOAD_PREFIX + plan)
            return super().get_observation(response)

    DirectPlanAgent.__name__ = f"DirectPlan{default_agent.__name__}"
    return DirectPlanAgent


def _extract_result(agent: Any, exception_name: str, exception_msg: str) -> str | None:
    """Extract the agent's final output based on how it terminated.

    On a clean ``Submitted`` exit, ``exception_msg`` is already the
    submitted text with the ``COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT``
    marker line stripped by ``DefaultAgent.has_finished`` — no extra
    sanitisation is needed. Any other exit (e.g. ``LimitsExceeded``)
    returns None so the pipeline can raise a clear error instead of
    passing an unfinished / invalid plan downstream.
    """
    if exception_name == "Submitted":
        return exception_msg
    return None


def _direct_plan_markdown_error(plan: str) -> str | None:
    """Return a format error without changing the submitted Plan."""

    if not plan or not plan.strip():
        return "the submitted Plan is empty"
    if not plan.startswith("# Plan\n"):
        return "the Plan must start exactly with '# Plan' followed by a newline"
    for residue in _PROTOCOL_RESIDUE:
        if residue in plan:
            return f"the Plan contains tool-protocol residue {residue!r}"

    positions: list[int] = []
    for heading in _DIRECT_PLAN_HEADINGS:
        matches = list(re.finditer(rf"(?m)^{re.escape(heading)}\s*$", plan))
        if len(matches) != 1:
            return f"the Plan must contain exactly one {heading!r} heading"
        positions.append(matches[0].start())
    if positions != sorted(positions):
        return "the Plan headings are not in N/R/P/V order"

    for index, heading in enumerate(_DIRECT_PLAN_HEADINGS[1:], start=1):
        start = positions[index] + len(heading)
        end = positions[index + 1] if index + 1 < len(positions) else len(plan)
        if not plan[start:end].strip():
            return f"the {heading!r} section is empty"
    return None


def _read_plan_from_file(env: Any) -> str | None:
    """Try to read /tmp/plan.md from the Docker container.

    Returns:
        The file content if successfully read and non-empty, otherwise None.
    """
    try:
        result = env.execute("cat /tmp/plan.md")
        if result.get("returncode") == 0:
            # Machine-readable Plan artifacts use stdout as their authority.
            # Apptainer diagnostics belong to stderr and must not be appended
            # to JSON or Plan text consumed by the host.
            content = str(result.get("stdout", result.get("output", "")))
            if content.strip():
                return content
    except Exception:
        pass
    return None


def run(
    config: Config,
    issue_description: str,
    env: Any,
    *,
    planning_rules: str = "",
    model_wrapper: Callable[[Any], Any] | None = None,
    failure_trajectory_path: Path | None = None,
    require_direct_submission: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the plan generation agent.

    Args:
        config: Full configuration object.
        issue_description: The SWE-bench issue description. Forwarded to
            DefaultAgent as ``task``; the configured
            ``plan_instance_template`` Jinja-renders ``{{task}}`` into the
            first user message.
        env: Docker environment wrapper (passed to DefaultAgent for tool
            execution).
        planning_rules: Optional candidate planning rules for online GEPA
            experiments. Existing PCT configs do not reference this variable,
            so the default preserves the historical prompt exactly.
        model_wrapper: Optional hook for callers that need to instrument model
            calls. The default preserves the historical model object.

    Returns:
        A tuple of ``(plan_text, trajectory_messages)``.

    Raises:
        TaskError: If the agent produces an empty or invalid plan.
        FatalError: If mini-swe-agent is not installed.
    """
    DefaultAgent, LitellmModel, _ = import_minisweagent()
    from minisweagent.agents.default import Submitted

    PlanAgent = (
        _direct_plan_agent_class(DefaultAgent, Submitted)
        if require_direct_submission
        else DefaultAgent
    )

    # Pass the raw system_template verbatim. The {{nrpv_block}} Jinja
    # placeholder is rendered at agent.run() time via the extra_template_vars
    # kwarg below — never inlined into the template source on the host side
    # (that would cause mini-swe-agent's second-pass Jinja render to crash
    # on any nrpv content with {{...}} or {%...%} fragments).
    system_template = config.prompts.plan_generation_prompt
    instance_template = config.prompts.plan_instance_template or None

    model = build_model(
        LitellmModel,
        model_name=config.system.model,
        api_key=config.api_key,
        api_base=config.system.api_base,
        temperature=config.agent.temperature,
    )
    if model_wrapper is not None:
        model = model_wrapper(model)

    agent_kwargs: dict[str, Any] = {}
    if require_direct_submission:
        agent_kwargs["action_protocol"] = PLAN_ACTION_PROTOCOL
    agent = build_default_agent(
        PlanAgent,
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
        instance_template=instance_template,
        **agent_kwargs,
    )

    logger.info(
        "Starting plan agent: model=%s step_limit=%s",
        config.system.model,
        config.agent.max_steps,
    )

    try:
        exception_name, exception_msg = agent.run(
            task=issue_description,
            nrpv_block=config.prompts.nrpv_block,
            planning_rules=planning_rules,
        )
    except CommandTimeoutError as exc:
        raise AgentTaskError(
            str(exc),
            phase="plan",
            reason="plan_command_timeout",
            trajectory=agent.messages,
        ) from exc
    raise_for_permanent_provider_error(exception_name, exception_msg)

    submitted_text = _extract_result(agent, exception_name, exception_msg)
    direct_submission = bool(
        submitted_text is not None
        and submitted_text.startswith(_DIRECT_PLAN_PAYLOAD_PREFIX)
    )
    if direct_submission:
        # Safe PCE authority: exact model terminal text, intercepted before
        # action parsing and therefore never reconstructed through /tmp.
        plan_text = submitted_text[len(_DIRECT_PLAN_PAYLOAD_PREFIX) :]
        format_error = _direct_plan_markdown_error(plan_text)
        if format_error is not None:
            _write_failure_trajectory(
                failure_trajectory_path,
                agent.messages,
                exception_name,
                exception_msg,
            )
            raise AgentTaskError(
                f"Planner direct submission is not valid standalone Markdown: "
                f"{format_error}.",
                phase="plan",
                reason="plan_invalid_markdown",
                trajectory=agent.messages,
            )
    elif require_direct_submission and exception_name == "Submitted":
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
        raise AgentTaskError(
            "Planner used the legacy stdout submission path; Safe PCE requires "
            "a direct FINAL_PLAN terminal response.",
            phase="plan",
            reason="plan_direct_submission_required",
            trajectory=agent.messages,
        )
    else:
        # Preserve legacy Online/GEPA/PCE semantics outside the explicitly
        # fingerprinted Safe PCE protocol. Those frozen experiments preferred
        # their phase-local file and are not silently migrated here.
        plan_text = _read_plan_from_file(env)
        if plan_text is None:
            plan_text = submitted_text

    if not plan_text or not plan_text.strip():
        _write_failure_trajectory(
            failure_trajectory_path, agent.messages, exception_name, exception_msg
        )
        if exception_name == "Submitted":
            raise AgentTaskError(
                "Plan agent submitted but /tmp/plan.md was empty and no plan text was returned.",
                phase="plan",
                reason="plan_empty",
                trajectory=agent.messages,
            )
        reason = (
            "plan_step_or_cost_limit"
            if exception_name == "LimitsExceeded"
            else "plan_not_submitted"
        )
        raise AgentTaskError(
            f"Plan agent terminated without a submission (exit_status={exception_name}). "
            f"Expected the agent to write a plan to /tmp/plan.md and finish with: "
            f"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
            phase="plan",
            reason=reason,
            trajectory=agent.messages,
        )

    return plan_text, agent.messages


def _write_failure_trajectory(
    path: Path | None,
    messages: list[dict[str, Any]],
    exit_status: str,
    exit_message: str,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "exit_status": exit_status,
                "exit_message": exit_message,
                "messages": messages,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
