"""Plan generation agent.

Uses DefaultAgent's interactive step loop so the agent can explore the
codebase (via cat, grep, ls, etc.) before producing the structured Plan.
Safe-PCE prompts terminate with a direct ``FINAL_PLAN`` response intercepted
before the shell action parser. The current bounded protocol delimits the Plan
with ``END_PLAN`` so provider text after that marker is retained in the raw
trajectory but is not part of Plan authority. Legacy prompts may still submit
through the ordinary mini-swe stdout marker and retain their historical
phase-local-file preference for frozen-run compatibility.
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
DIRECT_PLAN_END_MARKER = "END_PLAN"
_DIRECT_PLAN_PAYLOAD_PREFIX = "__VIBE_DIRECT_PLAN_V1__\n"
_DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX = "__VIBE_DIRECT_PLAN_V2__\n"
_DIRECT_PLAN_HEADINGS = (
    "# Plan",
    "## Navigation (N)",
    "## Reproduction (R)",
    "## Patch (P)",
    "## Validation (V)",
)
DIRECT_NRPV_PROTOCOL = "direct_final_plan_v1"
DIRECT_MARKDOWN_PROTOCOL = "direct_final_markdown_v2"
DIRECT_MARKDOWN_TEMPLATE_PROTOCOL = "direct_final_markdown_v3"
DIRECT_BOUNDED_MARKDOWN_PROTOCOL = "direct_final_markdown_v4"
DIRECT_MARKDOWN_PLAN_PLACEHOLDER = "[[TASK_SPECIFIC_MARKDOWN_PLAN]]"
_PROTOCOL_RESIDUE = (
    "</parameter>",
    "<berkeleyos>",
    "</berkeleyos>",
    "<result>",
    "</result>",
    "<｜｜DSML｜｜",
    "</｜｜DSML｜｜",
)
_PROTOCOL_RESIDUE_LINES = ("</description>",)

PLAN_ACTION_PROTOCOL = """\
## Planner action and final-submission protocol

During repository exploration, return exactly one executable bash block per
response. The parser executes the shell body captured from that block. Wait for
its real observation before choosing the next action.

When the Plan is complete, do not execute another command and do not write the
Plan to a file. Return one terminal response using exactly this structure,
replacing the descriptive lines with the complete Plan:

FINAL_PLAN
# Plan

## Navigation (N)
Repository locations and relevant behavior.

## Reproduction (R)
How to observe the current and expected behavior.

## Patch (P)
The proposed implementation changes.

## Validation (V)
Focused checks for the proposed change.

The terminal response must contain no text outside that structure and no bash
action block. `FINAL_PLAN` and every heading shown above must each occur exactly
once and in that order. A malformed terminal response is rejected and no Plan
artifact is saved. The Host intercepts a valid response before shell parsing
and preserves the Plan text verbatim.
"""

MARKDOWN_PLAN_ACTION_PROTOCOL = """\
## Planner action and final-submission protocol

During repository exploration, return exactly one executable bash block per
response. The parser executes the shell body captured from that block. Wait for
its real observation before choosing the next action.

When the Plan is complete, do not execute another command and do not write the
Plan to a file. The terminal response must begin with these exact two lines:

FINAL_PLAN
# Plan

Continue after `# Plan` with the complete task-specific Markdown Plan. No fixed
subsection headings are required.

The terminal response must contain no text outside the Plan and no bash action
block. `FINAL_PLAN` and `# Plan` must each occur exactly once. The Plan must
contain substantive content after `# Plan`. A malformed terminal response is
rejected and no Plan artifact is saved. The Host intercepts a valid response
before shell parsing and preserves the Plan text verbatim.
"""

MARKDOWN_PLAN_TEMPLATE_ACTION_PROTOCOL = f"""\
## Planner action and final-submission protocol

During repository exploration, return exactly one executable bash block per
response. The parser executes the shell body captured from that block. Wait for
its real observation before choosing the next action.

When the Plan is complete, do not execute another command and do not write the
Plan to a file. Copy this terminal-response template exactly, replacing the
placeholder with the complete task-specific Markdown Plan body:

FINAL_PLAN
# Plan

{DIRECT_MARKDOWN_PLAN_PLACEHOLDER}

Keep the first two lines exactly as shown. Remove the placeholder itself. The
replacement may use task-appropriate Markdown headings; no fixed subsection
headings are required. End the response when the Plan ends. There is no closing
marker and no text may appear outside the Plan.

Before submitting, inspect the complete response. Submit only when it matches
the template with the placeholder fully replaced, contains substantive
task-specific content after `# Plan`, and contains no text outside the Plan,
bash action block, tool-call wrapper, XML or DSML tag, simulated observation,
or protocol text. A malformed terminal response is rejected and no Plan
artifact is saved. Correct any problem before submitting; the Host validates
but never edits the response. A valid Plan is intercepted before shell parsing
and preserved verbatim.
"""

BOUNDED_MARKDOWN_PLAN_ACTION_PROTOCOL = f"""\
## Planner action and final-submission protocol

During repository exploration, return exactly one executable bash block per
response. The parser executes the shell body captured from that block. Wait for
its real observation before choosing the next action.

When the Plan is complete, do not execute another command and do not write the
Plan to a file. Copy this terminal-response template exactly, replacing the
placeholder with the complete task-specific Markdown Plan body:

FINAL_PLAN
# Plan

{DIRECT_MARKDOWN_PLAN_PLACEHOLDER}
END_PLAN

Keep `FINAL_PLAN`, `# Plan`, and `END_PLAN` exactly as shown, with each marker on
its own line. Remove the placeholder itself. The replacement may use
task-appropriate Markdown headings; no fixed subsection headings are required.
Finish the intended response at `END_PLAN`.

Before submitting, inspect the complete response. Submit only when the text
between `FINAL_PLAN` and `END_PLAN` starts with `# Plan`, contains substantive
task-specific content, and contains no bash action block, tool-call wrapper,
XML or DSML tag, simulated observation, or protocol text. A missing or malformed
boundary is rejected and no Plan artifact is saved. Correct any problem before
submitting; the Host validates but never edits the bounded Plan. The complete
terminal response remains raw audit evidence, while only the exact text between
the markers becomes Plan authority.
"""


def _direct_plan_agent_class(
    default_agent: type,
    submitted: type,
    *,
    bounded: bool = False,
) -> type:
    """Add a Plan-only terminal response without changing mini-swe-agent."""

    class DirectPlanAgent(default_agent):
        def get_observation(self, response: dict[str, Any]) -> dict[str, Any]:
            content = str(response.get("content", ""))
            candidate = content.lstrip()
            marker = DIRECT_PLAN_MARKER + "\n"
            if candidate.startswith(marker):
                if bounded:
                    # Preserve the full provider response. The Host extracts
                    # and validates the bounded Plan after shell interception.
                    raise submitted(_DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX + content)
                plan = candidate[len(marker) :]
                raise submitted(_DIRECT_PLAN_PAYLOAD_PREFIX + plan)
            return super().get_observation(response)

    DirectPlanAgent.__name__ = f"DirectPlan{default_agent.__name__}"
    return DirectPlanAgent


def _extract_bounded_direct_plan(raw_submission: str) -> tuple[str, str | None]:
    """Extract exact Plan bytes without rewriting the provider response."""

    candidate = raw_submission.lstrip()
    start_marker = DIRECT_PLAN_MARKER + "\n"
    if not candidate.startswith(start_marker):
        return "", "the terminal response must start with 'FINAL_PLAN'"
    body = candidate[len(start_marker) :]
    end_match = re.search(rf"(?m)^{re.escape(DIRECT_PLAN_END_MARKER)}$", body)
    if end_match is None:
        return "", "the terminal response is missing an exact 'END_PLAN' line"
    return body[: end_match.start()], None


def direct_plan_terminal_response(
    messages: list[dict[str, Any]],
) -> str | None:
    """Return the exact final direct-submission response from a trajectory."""

    marker = DIRECT_PLAN_MARKER + "\n"
    for message in reversed(messages):
        content = str(message.get("content", ""))
        if message.get("role") == "assistant" and content.lstrip().startswith(marker):
            return content
    return None


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


def _direct_plan_markdown_error(
    plan: str,
    *,
    require_nrpv: bool = True,
    forbidden_placeholder: str | None = None,
) -> str | None:
    """Return a format error without changing the submitted Plan."""

    if not plan or not plan.strip():
        return "the submitted Plan is empty"
    if not plan.startswith("# Plan\n"):
        return "the Plan must start exactly with '# Plan' followed by a newline"
    for residue in _PROTOCOL_RESIDUE:
        if residue in plan:
            return f"the Plan contains tool-protocol residue {residue!r}"
    plan_lines = {line.strip() for line in plan.splitlines()}
    for residue in _PROTOCOL_RESIDUE_LINES:
        if residue in plan_lines:
            return f"the Plan contains tool-protocol residue {residue!r}"
    if forbidden_placeholder is not None and forbidden_placeholder in plan:
        return "the Plan still contains the unexpanded template placeholder"

    plan_headings = list(re.finditer(r"(?m)^# Plan\s*$", plan))
    if len(plan_headings) != 1:
        return "the Plan must contain exactly one '# Plan' heading"
    if not plan[plan_headings[0].end() :].strip():
        return "the Plan contains no substantive content after '# Plan'"
    if not require_nrpv:
        return None

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
    direct_submission_protocol: str = DIRECT_NRPV_PROTOCOL,
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

    bounded_direct_submission = (
        direct_submission_protocol == DIRECT_BOUNDED_MARKDOWN_PROTOCOL
    )
    PlanAgent = (
        _direct_plan_agent_class(
            DefaultAgent,
            Submitted,
            bounded=bounded_direct_submission,
        )
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
        if direct_submission_protocol == DIRECT_NRPV_PROTOCOL:
            agent_kwargs["action_protocol"] = PLAN_ACTION_PROTOCOL
        elif direct_submission_protocol == DIRECT_MARKDOWN_PROTOCOL:
            agent_kwargs["action_protocol"] = MARKDOWN_PLAN_ACTION_PROTOCOL
        elif direct_submission_protocol == DIRECT_MARKDOWN_TEMPLATE_PROTOCOL:
            agent_kwargs["action_protocol"] = MARKDOWN_PLAN_TEMPLATE_ACTION_PROTOCOL
        elif direct_submission_protocol == DIRECT_BOUNDED_MARKDOWN_PROTOCOL:
            agent_kwargs["action_protocol"] = BOUNDED_MARKDOWN_PLAN_ACTION_PROTOCOL
        else:
            raise ValueError(
                f"unsupported direct Plan submission protocol: "
                f"{direct_submission_protocol}"
            )
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
        and (
            submitted_text.startswith(_DIRECT_PLAN_PAYLOAD_PREFIX)
            or submitted_text.startswith(_DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX)
        )
    )
    if direct_submission:
        boundary_error: str | None = None
        if submitted_text.startswith(_DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX):
            raw_submission = submitted_text[
                len(_DIRECT_PLAN_ENVELOPE_PAYLOAD_PREFIX) :
            ]
            plan_text, boundary_error = _extract_bounded_direct_plan(raw_submission)
        else:
            # Historical direct authority: exact terminal text after the
            # opening marker, intercepted before action parsing.
            plan_text = submitted_text[len(_DIRECT_PLAN_PAYLOAD_PREFIX) :]
        format_error = boundary_error or _direct_plan_markdown_error(
            plan_text,
            require_nrpv=(direct_submission_protocol == DIRECT_NRPV_PROTOCOL),
            forbidden_placeholder=(
                DIRECT_MARKDOWN_PLAN_PLACEHOLDER
                if direct_submission_protocol
                in {
                    DIRECT_MARKDOWN_TEMPLATE_PROTOCOL,
                    DIRECT_BOUNDED_MARKDOWN_PROTOCOL,
                }
                else None
            ),
        )
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
