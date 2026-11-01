"""Opt-in PCE Agent transport; scheduling, checkpoints and evaluation stay with PCE.

mini-SWE is the default and is returned unchanged. Native Codex uses the shared
SIF runtime and returns the same exact Plan, configured patch payload and
trajectory interfaces.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re

from jinja2 import StrictUndefined, Template

from src.agents.output_contract import AgentOutputContractError
from src.agents.runtime import codex_options, executor_name, run_agent
from src.agents.plan_submission import (validate_human_plan, validate_code_submission,
    SPLIT_CODE_PROTOCOL, SPLIT_CODE_CONTRACT, validate_split_code_submission, split_patch_digests)
from src.environment.source_access import extract_http_urls
from src.exceptions import AgentTaskError
from src.optimization.hpc.task_batch import atomic_json


PLAN_PROTOCOL = """Use the native tools to investigate the task. Finish with the
standalone Plan as your final response, bounded by these exact lines:
START_PLAN
# Plan
<the task-specific Markdown Plan>
END_PLAN
"""

CODE_PROTOCOL = """Use the native tools to implement and validate the supplied Plan.
The Git staging area is the implementation-submission boundary. Create the
submission artifact with this command after selecting the staged files:
git diff --cached --binary --full-index > /agent-output/submission.patch
Finish with this exact final response:
COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
"""


def load_agent_backends(raw: dict, *, plan_protocol: str) -> dict:
    """Keep Codex-specific options outside the unchanged mini-SWE ModelConfig."""
    result = {}
    for role in ("plan", "code"):
        value = raw[role]
        executor = executor_name(value)
        if executor == "mini_swe":
            continue
        if plan_protocol != "direct_human_markdown_v5":
            raise ValueError("Codex PCE requires the Safe PCE direct_human_markdown_v5 boundary")
        result[role] = codex_options(value)
    return result


def agent_runner(default, *, role, config, case, workspace, attempt_dir,
                 source_access_path, usage):
    """Select only the Agent. Callers retain their original phase lifecycle."""
    backend = getattr(config, "agent_backends", {}).get(role, {})
    def invoke(*args, **kwargs):
        safe = getattr(config, "plan_submission_protocol", "") == "direct_human_markdown_v5"
        if safe:
            # Legacy fields remain loadable for historical analysis. Current
            # Safe PCE Agents use the enclosing Worker deadline on both backends.
            agent_config = replace(args[0], agent=replace(args[0].agent,
                max_steps=0, cost_limit=0.0, timeout=0))
            args = (agent_config, *args[1:])
            # The Worker persists failure trajectories in its retry envelope.
            # Do not maintain separate Plan/Code failure copies in Safe PCE.
            kwargs = dict(kwargs, failure_trajectory_path=None)
            if role == "code":
                kwargs = dict(kwargs, phase_timeout_seconds=None)
            usage.write_optional("agent_execution_policy", phase=role, executor=executor_name(backend),
                        step_limit=0, cost_limit=0, command_timeout=None, phase_timeout=None,
                        deadline_owner="worker")
        def native():
            context = dict(backend=backend, sif_path=Path(case.image.sif_path),
                           workspace=workspace, attempt_dir=attempt_dir / f"codex_{role}",
                           workdir=config.docker.workdir, source_access_path=source_access_path,
                           usage=usage, instance_id=case.instance_id)
            context["tool_network_disabled"] = getattr(config, "agent_network_disabled", False)
            return (_run_plan if role == "plan" else _run_code)(*args, **kwargs, **context)
        def mini():
            if not safe:
                return default(*args, **kwargs)
            index = 2 if role == "plan" else 3
            mini_args = list(args)
            mini_args[index] = _AgentEnvironment(args[index])
            mini_kwargs = dict(kwargs, **({"validate_submission_in_loop": True} if role == "code" else {}))
            return default(*mini_args, **mini_kwargs)
        return run_agent(backend, mini_swe=mini, codex_cli=native)
    return invoke


class _AgentEnvironment:
    """Only the Agent command view is unbounded; Host preparation is unchanged."""
    def __init__(self, environment):
        self.environment = environment

    def __getattr__(self, name):
        return getattr(self.environment, name)

    def execute(self, command, *args, **kwargs):
        return self.environment.execute(command, *args, **dict(kwargs, timeout=0))


def _execute(config, issue, env, *, phase, variables, backend, sif_path, workspace,
             attempt_dir, workdir, source_access_path, usage, instance_id, allow_empty_submission=False,
             tool_network_disabled=False, submission_protocol="staged_diff_v1"):
    from src.optimization.codex_cli_runtime import CodexSIFExecution, run_codex_text_agent
    system_template = (config.prompts.plan_generation_prompt if phase == "plan"
                       else config.prompts.code_generation_prompt)
    instance_template = (config.prompts.plan_instance_template if phase == "plan"
                         else config.prompts.code_instance_template)
    values = dict(task=issue, nrpv_block=config.prompts.nrpv_block, **variables)
    system = Template(system_template, undefined=StrictUndefined).render(**values)
    user = Template(instance_template, undefined=StrictUndefined).render(**values)
    split = phase == "code" and submission_protocol == SPLIT_CODE_PROTOCOL
    if variables.get("code_retry_context"):
        user += "\n<previous_submission>\n" + variables["code_retry_context"] + "\n</previous_submission>"
    user += "\n" + (PLAN_PROTOCOL if phase == "plan" else
        SPLIT_CODE_CONTRACT + "\nWrite the completed JSON to /agent-output/submission.json. "
        "Finish with exactly COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT." if split else CODE_PROTOCOL)
    # Only explicit decision-time inputs enter this mount, never a case row,
    # reference patch/test authority, another phase workspace or whole run root.
    evidence = attempt_dir / "inputs"
    evidence.mkdir(parents=True, exist_ok=True)
    atomic_json(evidence / "manifest.json", {"schema_version": 1, "role": phase,
                "files": ["task.json"]})
    atomic_json(evidence / "task.json", dict(task=issue, **variables))
    boundary = CodexSIFExecution(sif_path=sif_path, evidence_dir=evidence,
        repository_dir=workspace, repository_writable=True, repository_cwd=workdir,
        tool_network_disabled=tool_network_disabled,
        source_policy={"prompt_urls": list(extract_http_urls(issue)),
                       "submission_contract": {"phase": phase, "allow_empty": allow_empty_submission,
                                               "workdir": workdir, "protocol": submission_protocol},
                       "context": {"phase": phase, "instance_id": instance_id}},
        output_files=("codex_final_response.txt", "submission.json" if split else "submission.patch") if phase == "code"
                     else ("codex_final_response.txt",))
    try:
        raw, trajectory = run_codex_text_agent(model_config=backend, container=boundary,
            working_directory=workspace, attempt_dir=attempt_dir, system=system, user=user,
            task="Prepare the Plan." if phase == "plan" else "Implement the supplied Plan.",
            evidence_manifest_path=evidence / "manifest.json")
    except AgentOutputContractError as exc:
        raise AgentTaskError(str(exc), phase=phase, reason="missing_final_submission",
                             trajectory=getattr(exc, "trajectory", [])) from exc
    usage.write_optional("codex_agent_completed", phase=phase, executor="codex_cli", model=backend["model"],
                reasoning_effort=backend["reasoning_effort"])
    if any(event.get("type") in {"turn.failed", "error"}
           for message in trajectory if message.get("role") == "codex_cli"
           for event in message["content"].get("events", [])):
        raise AgentTaskError("Codex turn did not complete successfully", phase=phase,
                             reason="codex_turn_failed", trajectory=trajectory)
    return raw, trajectory


def _run_plan(config, issue, env, *, planning_rules="", model_wrapper=None,
              failure_trajectory_path=None, require_direct_submission=True,
              direct_submission_protocol="direct_human_markdown_v5", **context):
    del model_wrapper, failure_trajectory_path
    if not require_direct_submission or direct_submission_protocol != "direct_human_markdown_v5":
        raise ValueError("Native PCE Planner requires the Safe PCE direct boundary")
    raw, trajectory = _execute(config, issue, env, phase="plan",
                              variables={"planning_rules": planning_rules}, **context)
    try:
        plan = validate_human_plan(raw)
    except ValueError as exc:
        raise AgentTaskError(str(exc), phase="plan", reason="plan_invalid_markdown", trajectory=trajectory) from exc
    return plan, trajectory


def _run_code(config, plan, issue, env, *, model_wrapper=None, failure_trajectory_path=None,
              phase_timeout_seconds=None, allow_empty_submission=False,
              submission_protocol="staged_diff_v1", code_retry_context="", **context):
    del model_wrapper, failure_trajectory_path
    # Native CLI is owned by the same Worker/Slurm deadline; no nested retry loop.
    del phase_timeout_seconds
    raw, trajectory = _execute(config, issue, env, phase="code",
        variables={"plan": plan, **({"code_retry_context": code_retry_context} if code_retry_context else {})},
        allow_empty_submission=allow_empty_submission, submission_protocol=submission_protocol, **context)
    if submission_protocol == SPLIT_CODE_PROTOCOL:
        path = context["attempt_dir"] / "submission.json"
        try:
            if raw.strip() != "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
                raise ValueError("the terminal response must be exactly COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
            if not path.is_file() or path.is_symlink():
                raise ValueError("submission.json must be a regular file with both patches")
            patches = validate_split_code_submission(path.read_bytes(), allow_empty=allow_empty_submission,
                                                     digests=split_patch_digests(env))
        except ValueError as exc:
            raise AgentTaskError(str(exc), phase="code", reason="code_invalid_split_submission",
                                 trajectory=trajectory) from exc
        except (OSError, RuntimeError) as exc:
            exc.trajectory = trajectory
            raise
        return patches, trajectory
    patch_path = context["attempt_dir"] / "submission.patch"
    patch_bytes = patch_path.read_bytes() if patch_path.is_file() and not patch_path.is_symlink() else None
    try:
        validate_code_submission(raw, patch_bytes, allow_empty=allow_empty_submission)
    except ValueError as exc:
        reason = "code_empty_patch" if patch_bytes is not None and not patch_bytes.strip() else "code_not_submitted"
        raise AgentTaskError(str(exc), phase="code", reason=reason, trajectory=trajectory) from exc
    # A mismatch is rejected, never replaced with a Host-derived diff.
    try:
        diff = env.execute("set -o pipefail; git diff --cached --binary --full-index | sha256sum")
    except Exception as exc:
        exc.trajectory = trajectory
        raise
    digest = str(diff.get("stdout", diff.get("output", ""))).split()
    if (diff.get("returncode") != 0 or not digest
            or re.fullmatch(r"[0-9a-f]{64}", digest[0]) is None):
        error = RuntimeError("cannot verify the submitted patch against its staged diff "
                             f"(returncode={diff.get('returncode')})")
        error.trajectory = trajectory
        raise error
    try:
        validate_code_submission(raw, patch_bytes, allow_empty=allow_empty_submission, staged_sha256=digest[0])
    except ValueError as exc:
        raise AgentTaskError(str(exc), phase="code", reason="code_submission_mismatch", trajectory=trajectory) from exc
    # PCE's code checkpoint records the verified hash and unchanged patch.
    return patch_bytes.decode("utf-8"), trajectory
