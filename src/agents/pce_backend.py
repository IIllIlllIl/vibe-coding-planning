"""Opt-in PCE Agent transport; scheduling, checkpoints and evaluation stay with PCE.

mini-SWE is the default and is returned unchanged. Native Codex uses the shared
SIF runtime and returns the same exact Plan/patch and trajectory interfaces.
"""
from __future__ import annotations

from functools import partial
import hashlib
from pathlib import Path
import shlex

from jinja2 import StrictUndefined, Template

from src.agents import plan_agent
from src.agents.runtime import codex_options, executor_name, run_agent
from src.environment.source_access import (
    append_source_access_event, classify_source_access, extract_http_urls,
)
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
        def native():
            context = dict(backend=backend, sif_path=Path(case.image.sif_path),
                           workspace=workspace, attempt_dir=attempt_dir / f"codex_{role}",
                           workdir=config.docker.workdir, source_access_path=source_access_path,
                           usage=usage, instance_id=case.instance_id)
            return (_run_plan if role == "plan" else _run_code)(*args, **kwargs, **context)
        return run_agent(backend, mini_swe=partial(default, *args, **kwargs), codex_cli=native)
    return invoke


def _audit_sources(trajectory, *, issue, path, phase, instance_id):
    """Post-execution audit, not the mini-SWE pre-execution interceptor.

Commands with disallowed acquisition invalidate the completion before its
scientific checkpoint. Native events remain the raw authority, including on
failure. This does not claim prevention or complete network surveillance.
"""
    commands = {}
    unsupported = False
    for message in trajectory:
        if message.get("role") != "codex_cli":
            continue
        content = message["content"]
        if content.get("malformed_event_lines"):
            unsupported = True
        for event in content.get("events", []):
            item = event.get("item", {})
            if item.get("type") == "web_search":
                unsupported = True
            if item.get("type") == "command_execution":
                command = item.get("command")
                if not isinstance(command, str):
                    unsupported = True
                    continue
                commands[item.get("id", command)] = command
    blocked = False
    for command in commands.values():
        inspected = command
        try:
            words = shlex.split(command)
            if len(words) == 3 and Path(words[0]).name in {"bash", "sh", "zsh"} and words[1] in {"-c", "-lc"}:
                inspected = words[2]
        except ValueError:
            pass
        decision = classify_source_access(inspected, prompt_urls=frozenset(extract_http_urls(issue)))
        if decision is not None:
            append_source_access_event(path, {**decision, "phase": phase,
                "instance_id": instance_id, "executor": "codex_cli", "executed": True,
                "execution_status": "native_post_execution_audit"}, command=command)
            blocked |= decision["decision"] == "block"
    return blocked or unsupported


def _execute(config, issue, env, *, phase, variables, backend, sif_path, workspace,
             attempt_dir, workdir, source_access_path, usage, instance_id):
    from src.optimization.codex_cli_runtime import CodexSIFExecution, run_codex_text_agent
    system_template = (config.prompts.plan_generation_prompt if phase == "plan"
                       else config.prompts.code_generation_prompt)
    instance_template = (config.prompts.plan_instance_template if phase == "plan"
                         else config.prompts.code_instance_template)
    values = dict(task=issue, nrpv_block=config.prompts.nrpv_block, **variables)
    system = Template(system_template, undefined=StrictUndefined).render(**values)
    user = Template(instance_template, undefined=StrictUndefined).render(**values)
    user += "\n" + (PLAN_PROTOCOL if phase == "plan" else CODE_PROTOCOL)
    # Only explicit decision-time inputs enter this mount, never a case row,
    # reference patch/test authority, another phase workspace or whole run root.
    evidence = attempt_dir / "inputs"
    evidence.mkdir(parents=True, exist_ok=True)
    atomic_json(evidence / "manifest.json", {"schema_version": 1, "role": phase,
                "files": ["task.json"]})
    atomic_json(evidence / "task.json", dict(task=issue, **variables))
    boundary = CodexSIFExecution(sif_path=sif_path, evidence_dir=evidence,
        repository_dir=workspace, repository_writable=True, repository_cwd=workdir,
        output_files=("codex_final_response.txt", "submission.patch") if phase == "code"
                     else ("codex_final_response.txt",))
    try:
        raw, trajectory = run_codex_text_agent(model_config=backend, container=boundary,
            working_directory=workspace, attempt_dir=attempt_dir, system=system, user=user,
            task="Prepare the Plan." if phase == "plan" else "Implement the supplied Plan.",
            evidence_manifest_path=evidence / "manifest.json")
    except Exception as exc:
        trajectory = getattr(exc, "trajectory", [])
        _audit_sources(trajectory, issue=issue, path=source_access_path,
                       phase=phase, instance_id=instance_id)
        atomic_json(attempt_dir / "failure.json", {"error_type": type(exc).__name__,
                    "error": str(exc), "trajectory": trajectory})
        raise
    atomic_json(attempt_dir / "trajectory.json", trajectory)
    usage.write("codex_agent_completed", phase=phase, executor="codex_cli", model=backend["model"],
                reasoning_effort=backend["reasoning_effort"])
    if _audit_sources(trajectory, issue=issue, path=source_access_path,
                      phase=phase, instance_id=instance_id):
        raise AgentTaskError("Native Codex completion failed source-access/trace validation",
                             phase=phase, reason="codex_source_or_trace_rejected", trajectory=trajectory)
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
    plan, error = plan_agent._extract_bounded_direct_plan(raw, start_marker="START_PLAN", end_marker="END_PLAN")
    error = error or plan_agent._direct_plan_markdown_error(plan, require_nrpv=False)
    if error:
        raise AgentTaskError(str(error), phase="plan", reason="plan_invalid_markdown", trajectory=trajectory)
    return plan, trajectory


def _run_code(config, plan, issue, env, *, model_wrapper=None, failure_trajectory_path=None,
              phase_timeout_seconds=None, allow_empty_submission=False, **context):
    del model_wrapper, failure_trajectory_path
    # Native CLI is owned by the same Worker/Slurm deadline; no nested retry loop.
    # PolyBench's existing optional phase timer is shared rather than reimplemented.
    from src.agents.code_agent import _phase_timer
    with _phase_timer(phase_timeout_seconds):
        raw, trajectory = _execute(config, issue, env, phase="code", variables={"plan": plan}, **context)
    patch_path = context["attempt_dir"] / "submission.patch"
    if raw.strip() != "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" or not patch_path.is_file() or patch_path.is_symlink():
        raise AgentTaskError("Codex Coder did not submit its raw patch artifact", phase="code",
                             reason="code_not_submitted", trajectory=trajectory)
    patch = patch_path.read_bytes().decode("utf-8")
    if not patch.strip() and not allow_empty_submission:
        raise AgentTaskError("Codex Coder submitted an empty patch", phase="code",
                             reason="code_empty_patch", trajectory=trajectory)
    # A mismatch is rejected, never replaced with a Host-derived diff.
    diff = env.execute("set -o pipefail; git diff --cached --binary --full-index | sha256sum")
    digest = str(diff.get("stdout", diff.get("output", ""))).split()
    if diff.get("returncode") != 0 or not digest or digest[0] != hashlib.sha256(patch.encode()).hexdigest():
        raise AgentTaskError("Codex patch differs from its staged submission", phase="code",
                             reason="code_submission_mismatch", trajectory=trajectory)
    atomic_json(context["attempt_dir"] / "submission.json", {
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(), "host_patch_transformation": False})
    return patch, trajectory
