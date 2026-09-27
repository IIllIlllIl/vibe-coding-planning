"""Single-call prompt runtime used inside reject-playbook workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.environment.apptainer_env import ApptainerEnvironment, ApptainerSifCache
from src.environment.docker_env import DockerCapacityWindow
from src.environment.repository_baseline import restore_repository_to_base
from src.environment.repository_history import (
    RepositoryHistoryCache,
    install_repository_history_bundle,
)
from src.optimization.hpc.task_batch import atomic_json
from src.environment.source_access import (
    SOURCE_ACCESS_POLICY_VERSION,
    extract_http_urls,
    summarize_source_access_log,
)
from src.evaluator.swe_evaluator import derive_image_name


class PlaybookAgentOutputContractError(ValueError):
    """An Agent returned a final artifact that Host parsing cannot accept."""


class CheckerActionRecorder:
    """Record only Agent tool calls, excluding Host setup and artifact reads.

    Delegate the environment unchanged. The journal establishes that an
    observation was available, not that a citation is semantically correct.
    """

    def __init__(self, environment: Any) -> None:
        self.environment = environment
        self.actions: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.environment, name)

    def execute(self, command: str, **kwargs: Any) -> dict[str, Any]:
        output = self.environment.execute(command, **kwargs)
        lines = str(output.get("output", "")).lstrip().splitlines()
        terminal = bool(lines and lines[0].strip() in {
            "MINI_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        } and output.get("returncode", 0) == 0)
        self.actions.append({
            "command": command,
            "returncode": output.get("returncode", 0),
            "has_output": bool(str(output.get("output", "")).strip()),
            "terminal_submission": terminal,
        })
        return output


def validate_checker_observations(
    output: Mapping[str, Any], trajectory: list[dict[str, Any]],
) -> None:
    """Reject repository citations with no actual pre-submission observation.

    This intentionally does not require investigation for issue/Plan-only
    judgments or attempt to verify citations using an LLM or shell heuristics.
    """
    journal = [entry.get("content") for entry in trajectory
               if entry.get("role") == "host_checker_action_observations"]
    if len(journal) != 1 or not isinstance(journal[0], list):
        raise PlaybookAgentOutputContractError("Checker executed-action journal missing")
    observed = any(
        item.get("returncode") == 0 and item.get("has_output") is True
        and item.get("terminal_submission") is False
        for item in journal[0]
    )
    if observed:
        return
    for index, row in enumerate(output.get("rule_results", [])):
        for item in row.get("evidence", []):
            if item.get("source") == "repository":
                raise PlaybookAgentOutputContractError(
                    f"rule_results[{index}] (rule {row.get('rule_number')}) cites "
                    "repository evidence, but no successful tool observation was "
                    "received before submission. Rejected actions and Host setup "
                    "are not Agent observations. Inspect the needed material and "
                    "resubmit using actual observations; Host did not change the artifact."
                )


def evidence_agent_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the repository-free evidence environment shared by Curator.

    Older runs store explicit overrides under reflection. Repo Reflector no
    longer uses that environment, but Curator still does. The shared container
    cache is the default when those legacy overrides are absent.
    """
    legacy = config.get("reflection", {})
    cache = legacy.get("evidence_sif_cache_dir") or config.get("container", {}).get("sif_cache_dir")
    if not isinstance(cache, str) or not cache.strip():
        raise ValueError("Evidence Agent requires container.sif_cache_dir or reflection.evidence_sif_cache_dir")
    return {
        "evidence_sif_cache_dir": cache,
        "evidence_image": legacy.get("evidence_image", "python:3.12-slim"),
        "command_timeout_seconds": int(legacy.get("command_timeout_seconds", 1800)),
    }


def require_prepared_repository_history(
    image_authority: Mapping[str, Any], repository: Mapping[str, Any]
) -> tuple[Path, dict[str, Any]]:
    """Reuse the Safe PCE artifact; never repack inside a Repo Agent job."""
    cache = RepositoryHistoryCache(
        Path(str(image_authority["sif_path"])).parent.parent
        / "repository-history-cache-v1"
    )
    existing = cache.validate(
        sif_sha256=str(image_authority["sif_sha256"]),
        base_commit=str(repository["base_commit"]),
    )
    if existing is None:
        raise ValueError(
            "Prepared repository history is missing or invalid for "
            f"{repository['instance_id']}; prepare and verify the Safe PCE "
            "history bundle before submitting Repo Agent tasks."
        )
    return existing


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        stripped = match.group(1)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


def _uses_codex_cli(model_config: Mapping[str, Any]) -> bool:
    executor = str(model_config.get("executor", "mini_swe"))
    if executor not in {"mini_swe", "codex_cli"}:
        raise ValueError(f"unsupported playbook Agent executor: {executor}")
    return executor == "codex_cli"


class PromptModel:
    def __init__(self, model_config: Mapping[str, Any]) -> None:
        _, LitellmModel, _ = import_minisweagent()
        key_env = str(model_config.get("api_key_env", "DEEPSEEK_API_KEY"))
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ValueError(f"environment variable {key_env} is not set")
        self.model = build_model(
            LitellmModel,
            str(model_config["model"]),
            api_key,
            str(model_config.get("api_base", "https://api.deepseek.com")),
            float(model_config.get("temperature", 0.0)),
            **({"thinking": model_config["thinking"]} if "thinking" in model_config else {}),
            **({"reasoning_effort": model_config["reasoning_effort"]} if "reasoning_effort" in model_config else {}),
        )

    def __call__(
        self, system: str, user: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response = self.model.query(messages)
        assistant = {
            "role": "assistant",
            "content": response["content"],
            "extra": response.get("extra", {}),
        }
        trajectory = [*messages, assistant]
        try:
            return _json_object(response["content"]), trajectory
        except (ValueError, json.JSONDecodeError) as exc:
            error = PlaybookAgentOutputContractError(str(exc))
            error.raw_response = response["content"]  # type: ignore[attr-defined]
            error.trajectory = trajectory  # type: ignore[attr-defined]
            raise error from exc


def _run_evidence_json_agent(
    *,
    model_config: Mapping[str, Any],
    evidence_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    evidence_dir: str,
    task: str,
    artifact_name: str,
    prompt_values: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a tool-using JSON agent over a read-only, repository-free bundle."""
    DefaultAgent, LitellmModel, _ = import_minisweagent()
    key_env = str(model_config.get("api_key_env", "DEEPSEEK_API_KEY"))
    api_key = os.environ.get(key_env)
    if not api_key:
        raise ValueError(f"environment variable {key_env} is not set")
    model = build_model(
        LitellmModel,
        str(model_config["model"]),
        api_key,
        str(model_config.get("api_base", "https://api.deepseek.com")),
        float(model_config.get("temperature", 0.0)),
        **({"thinking": model_config["thinking"]} if "thinking" in model_config else {}),
        **({"reasoning_effort": model_config["reasoning_effort"]} if "reasoning_effort" in model_config else {}),
    )
    cache = Path(
        os.path.expandvars(str(evidence_config["evidence_sif_cache_dir"]))
    ).expanduser()
    capacity = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=1,
        min_free_gb=1,
        disk_path=cache,
        enable_docker_maintenance=False,
    )
    environment = ApptainerEnvironment(
        image=str(evidence_config.get("evidence_image", "python:3.12-slim")),
        cwd="/evidence",
        sif_cache_dir=cache,
        capacity_window=capacity,
        # Apptainer otherwise automatically binds the submitting working
        # directory. Reflectors receive only the explicit evidence bundle,
        # never the staged repository/controller tree.
        run_args=[
            "--no-mount",
            "cwd",
            "--pwd",
            "/evidence",
            "--bind",
            f"{Path(evidence_dir).resolve()}:/evidence:ro",
        ],
        timeout=int(evidence_config.get("command_timeout_seconds", 1800)),
        writable_tmpfs=True,
        network_disabled=True,
        isolate_tmp=True,
    )
    try:
        agent = build_default_agent(
            DefaultAgent,
            model,
            environment,
            system_template=system,
            instance_template=instance_template,
            # The Slurm task wall time owns the complete Agent-session limit.
            # Keep only the per-command environment timeout here; an internal
            # step cap can discard a valid artifact before atomic completion.
            step_limit=0,
        )
        exit_status, submission = agent.run(
            task=task,
            **dict(prompt_values),
        )
        raise_for_permanent_provider_error(exit_status, submission)
        if exit_status != "Submitted":
            error = RuntimeError(
                "Evidence agent ended without a submitted artifact "
                f"(exit_status={exit_status})"
            )
            error.trajectory = list(agent.messages)  # type: ignore[attr-defined]
            raise error
        # The submitted terminal observation may contain container diagnostics.
        # Treat the Agent-authored file as the data authority and read only its
        # stdout; stderr remains separate diagnostic evidence.
        artifact = environment.execute(
            f"cat /tmp/{artifact_name}",
            cwd="/evidence",
            timeout=int(evidence_config.get("command_timeout_seconds", 1800)),
        )
        trajectory = [
            *list(agent.messages),
            {
                "role": "host_artifact_read",
                "content": {
                    "path": f"/tmp/{artifact_name}",
                    "returncode": artifact["returncode"],
                    "stderr": artifact.get("stderr", ""),
                    "terminal_submission": submission,
                },
            },
        ]
        if artifact["returncode"] != 0:
            error = RuntimeError("Evidence-agent artifact could not be read")
            error.trajectory = trajectory  # type: ignore[attr-defined]
            raise error
        try:
            return _json_object(str(artifact.get("stdout", ""))), trajectory
        except (ValueError, json.JSONDecodeError) as exc:
            error = PlaybookAgentOutputContractError(str(exc))
            error.raw_response = artifact.get("stdout", "")  # type: ignore[attr-defined]
            error.trajectory = trajectory  # type: ignore[attr-defined]
            raise error from exc
    finally:
        environment.cleanup()


def run_evidence_reflector(
    *,
    model_config: Mapping[str, Any],
    reflection_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    evidence_dir: str,
    internal_playbook: str,
    retry_feedback: str = "",
    attempt_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a tool-using Reflector over a read-only, repository-free bundle."""
    if _uses_codex_cli(model_config):
        from src.optimization.codex_cli_runtime import CodexSIFExecution, run_codex_json_agent

        user = _render(
            instance_template,
            evidence_path="/evidence",
            internal_playbook=internal_playbook,
            retry_feedback=retry_feedback,
        )
        return run_codex_json_agent(
            model_config=model_config,
            working_directory=Path(evidence_dir),
            attempt_dir=(
                attempt_dir
                or Path(evidence_dir).parent / ".codex_reflector_attempt"
            ),
            system=system,
            user=user,
            task="Attribute this case to every active rejection rule.",
            evidence_manifest_path=Path(evidence_dir) / "manifest.json",
            container=CodexSIFExecution(
                sif_path=_codex_evidence_sif(reflection_config),
                evidence_dir=Path(evidence_dir),
            ),
        )
    return _run_evidence_json_agent(
        model_config=model_config,
        evidence_config=reflection_config,
        system=system,
        instance_template=instance_template,
        evidence_dir=evidence_dir,
        task="Attribute this case to every active rejection rule.",
        artifact_name="reflection.json",
        prompt_values={
            "evidence_path": "/evidence",
            "internal_playbook": internal_playbook,
            "retry_feedback": retry_feedback,
        },
    )


def run_evidence_curator(
    *,
    model_config: Mapping[str, Any],
    reflection_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    evidence_dir: str,
    counted_internal_playbook: str,
    case_count: int,
    retry_feedback: str = "",
    task: str = "Curate durable rejection concerns from the completed reflections.",
    attempt_dir: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the Curator over a file-backed cross-case reflection bundle."""
    if _uses_codex_cli(model_config):
        from src.optimization.codex_cli_runtime import CodexSIFExecution, run_codex_json_agent

        user = _render(
            instance_template,
            evidence_path="/evidence",
            counted_internal_playbook=counted_internal_playbook,
            case_count=case_count,
            retry_feedback=retry_feedback,
        )
        return run_codex_json_agent(
            model_config=model_config,
            working_directory=Path(evidence_dir),
            attempt_dir=(
                attempt_dir
                or Path(evidence_dir).parent / ".codex_curator_attempt"
            ),
            system=system,
            user=user,
            task=task,
            evidence_manifest_path=Path(evidence_dir) / "manifest.json",
            container=CodexSIFExecution(
                sif_path=_codex_evidence_sif(reflection_config),
                evidence_dir=Path(evidence_dir),
            ),
        )
    return _run_evidence_json_agent(
        model_config=model_config,
        evidence_config=reflection_config,
        system=system,
        instance_template=instance_template,
        evidence_dir=evidence_dir,
        task=task,
        artifact_name="curator.json",
        prompt_values={
            "evidence_path": "/evidence",
            "counted_internal_playbook": counted_internal_playbook,
            "case_count": case_count,
            "retry_feedback": retry_feedback,
        },
    )


def _codex_evidence_sif(config: Mapping[str, Any]) -> Path:
    """Use the prepared evidence SIF; never download inside a Codex worker."""
    cache = Path(os.path.expandvars(str(config["evidence_sif_cache_dir"]))).expanduser()
    capacity = DockerCapacityWindow(
        max_concurrent=1, max_cached_images=1, min_free_gb=1,
        disk_path=cache, enable_docker_maintenance=False,
    )
    path = ApptainerSifCache(cache, capacity).sif_path(
        str(config.get("evidence_image", "python:3.12-slim"))
    )
    if not path.is_file():
        raise ValueError("Codex evidence SIF must be prepared before Agent submission")
    return path


def _render(template: str, **values: Any) -> str:
    return (
        Environment(undefined=StrictUndefined, autoescape=False)
        .from_string(template)
        .render(**values)
    )


def _run_repository_json_agent(
    *,
    model_config: Mapping[str, Any],
    repository_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    repository: Mapping[str, Any],
    image_authority: Mapping[str, Any],
    attempt_dir: Path,
    task: str,
    artifact_name: str,
    prompt_values: Mapping[str, Any],
    source_access_issue: str,
    evidence_dir: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one tool-using Agent against a disposable frozen repository."""
    if repository_config.get("source_access_policy") != "conservative_blacklist_v3":
        raise ValueError(
            "Repo Agent requires the Safe PCE conservative_blacklist_v3 source boundary"
        )
    if set(repository) != {"repo", "base_commit", "instance_id"}:
        raise ValueError("Repo Agent repository identity has an invalid schema")
    if any(
        not isinstance(repository[key], str) or not str(repository[key]).strip()
        for key in repository
    ):
        raise ValueError("Repo Agent repository identity contains an empty value")
    expected_authority = {"requested_ref", "sif_path", "sif_sha256", "sif_bytes"}
    if set(image_authority) != expected_authority:
        raise ValueError("Repo Agent image authority has an invalid schema")
    expected_ref = derive_image_name(repository)
    if image_authority["requested_ref"] != expected_ref:
        raise ValueError("Repo Agent image authority does not match the case")

    codex_cli = _uses_codex_cli(model_config)
    if not codex_cli:
        DefaultAgent, LitellmModel, _ = import_minisweagent()
        key_env = str(model_config.get("api_key_env", "DEEPSEEK_API_KEY"))
        api_key = os.environ.get(key_env)
        if not api_key:
            raise ValueError(f"environment variable {key_env} is not set")
        model = build_model(
            LitellmModel,
            str(model_config["model"]),
            api_key,
            str(model_config.get("api_base", "https://api.deepseek.com")),
            float(model_config.get("temperature", 0.0)),
            **({"thinking": model_config["thinking"]} if "thinking" in model_config else {}),
            **({"reasoning_effort": model_config["reasoning_effort"]} if "reasoning_effort" in model_config else {}),
        )
    cache = Path(
        os.path.expandvars(str(repository_config["sif_cache_dir"]))
    ).expanduser()
    capacity = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=1,
        min_free_gb=1,
        disk_path=cache,
        enable_docker_maintenance=False,
    )
    cache_path = ApptainerSifCache(cache, capacity).sif_path(expected_ref)
    declared_path = Path(str(image_authority["sif_path"]))
    if cache_path != declared_path:
        raise ValueError("Repo Agent SIF path differs from frozen authority")
    if not declared_path.is_file():
        raise ValueError("Repo Agent frozen SIF is missing")
    if declared_path.stat().st_size != int(image_authority["sif_bytes"]):
        raise ValueError("Repo Agent frozen SIF size differs from authority")
    history_bundle, history_manifest = require_prepared_repository_history(
        image_authority, repository
    )

    workdir = str(repository_config.get("workdir", "/testbed"))
    timeout = int(repository_config.get("command_timeout_seconds", 1800))
    attempt_dir.mkdir(parents=True, exist_ok=True)
    baseline_dir = attempt_dir / "repository_baseline"
    source_access_path = attempt_dir / "source_access.jsonl"
    source_access_path.touch(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vibe-repo-agent-") as temporary:
        host_workdir = Path(temporary) / "repository"
        run_args = ["--containall", "--no-mount", "cwd"]
        if evidence_dir is not None:
            run_args.extend(["--bind", f"{Path(evidence_dir).resolve()}:/evidence:ro"])
        environment = ApptainerEnvironment(
            image=expected_ref,
            cwd=workdir,
            sif_cache_dir=cache,
            capacity_window=capacity,
            timeout=timeout,
            writable_tmpfs=True,
            network_disabled=False,
            git_safe_directories=[workdir],
            host_workdir=host_workdir,
            initialize_host_workdir=True,
            isolate_tmp=True,
            masked_container_paths=list(
                repository_config.get(
                    "masked_container_paths", ["/opt/miniconda3/pkgs"]
                )
            ),
            run_args=run_args,
        )
        try:
            install_evidence = install_repository_history_bundle(
                repository_dir=host_workdir,
                bundle=history_bundle,
                base_commit=str(repository["base_commit"]),
            )
            atomic_json(baseline_dir / "repository_history_install.json", install_evidence)
            atomic_json(baseline_dir / "repository_history_artifact.json", history_manifest)
            baseline = restore_repository_to_base(
                environment,
                str(repository["base_commit"]),
                phase=str(repository_config.get("phase", "repo_checker")),
                evidence_dir=baseline_dir,
                timeout=timeout,
                prune_future_history=False,
            )
            if (
                repository_config.get("review_only", False)
                and repository_config.get("phase") in {"repo_checker", "paired_repo_checker"}
            ):
                environment.make_repository_read_only()
            environment.enable_source_access_audit(
                prompt_urls=list(extract_http_urls(source_access_issue)),
                log_path=source_access_path,
                context={
                    "instance_id": repository["instance_id"],
                    "phase": repository_config.get("phase", "repo_checker"),
                },
            )
            if codex_cli:
                from src.optimization.codex_cli_runtime import CodexSIFExecution, run_codex_json_agent

                codex_values = dict(prompt_values)
                if evidence_dir is not None:
                    codex_values["evidence_path"] = "/evidence"
                if evidence_dir is None:
                    raise ValueError("Codex Repo Agent requires a task-scoped evidence bundle")
                user = _render(instance_template, **codex_values)
                output, trajectory = run_codex_json_agent(
                    model_config=model_config,
                    working_directory=host_workdir,
                    attempt_dir=attempt_dir,
                    system=system,
                    user=user,
                    task=task,
                    evidence_manifest_path=(
                        Path(evidence_dir) / "manifest.json"
                        if evidence_dir is not None
                        else None
                    ),
                    container=CodexSIFExecution(
                        sif_path=declared_path,
                        evidence_dir=Path(evidence_dir),
                        repository_dir=host_workdir,
                        masked_paths=tuple(repository_config.get(
                            "masked_container_paths", ["/opt/miniconda3/pkgs"]
                        )),
                    ),
                )
                trajectory.extend(
                    [
                        {
                            "role": "host_repository_baseline",
                            "content": {
                                "declared_base_commit": repository["base_commit"],
                                "observed_head": baseline["after"]["head"]["output"].strip(),
                                "sif_sha256_authority": image_authority["sif_sha256"],
                            },
                        },
                        {
                            "role": "host_source_access_audit",
                            "content": {
                                "policy_version": SOURCE_ACCESS_POLICY_VERSION,
                                "path": str(source_access_path),
                                "summary": summarize_source_access_log(source_access_path),
                                "coverage": "apptainer_environment_only",
                            },
                        },
                    ]
                )
                return output, trajectory
            is_checker = repository_config.get("phase") in {
                "repo_checker", "paired_repo_checker"
            }
            observation_contract = repository_config.get("observation_contract")
            if observation_contract not in {None, "executed_tools_v1"}:
                raise ValueError("unknown Checker observation contract")
            recorder = (
                CheckerActionRecorder(environment)
                if is_checker and observation_contract else None
            )
            agent = build_default_agent(
                DefaultAgent,
                model,
                recorder if recorder is not None else environment,
                system_template=system,
                instance_template=instance_template,
                step_limit=0,
            )
            exit_status, submission = agent.run(task=task, **dict(prompt_values))
            raise_for_permanent_provider_error(exit_status, submission)
            if exit_status != "Submitted":
                error = RuntimeError(
                    "Repo Agent ended without a submitted artifact "
                    f"(exit_status={exit_status})"
                )
                error.trajectory = list(agent.messages)  # type: ignore[attr-defined]
                raise error
            artifact = environment.execute(
                f"cat /tmp/{artifact_name}",
                cwd=workdir,
                timeout=timeout,
            )
            trajectory = [
                *list(agent.messages),
                *([{"role": "host_checker_action_observations", "content": recorder.actions}]
                  if recorder is not None else []),
                {
                    "role": "host_repository_baseline",
                    "content": {
                        "declared_base_commit": repository["base_commit"],
                        "observed_head": baseline["after"]["head"]["output"].strip(),
                        "sif_sha256_authority": image_authority["sif_sha256"],
                    },
                },
                {
                    "role": "host_artifact_read",
                    "content": {
                        "path": f"/tmp/{artifact_name}",
                        "returncode": artifact["returncode"],
                        "stderr": artifact.get("stderr", ""),
                        "terminal_submission": submission,
                    },
                },
                {
                    "role": "host_source_access_audit",
                    "content": {
                        "policy_version": SOURCE_ACCESS_POLICY_VERSION,
                        "path": str(source_access_path),
                        "summary": summarize_source_access_log(source_access_path),
                    },
                },
            ]
            if artifact["returncode"] != 0:
                error = RuntimeError("Repo Agent artifact could not be read")
                error.trajectory = trajectory  # type: ignore[attr-defined]
                raise error
            try:
                return _json_object(str(artifact.get("stdout", ""))), trajectory
            except (ValueError, json.JSONDecodeError) as exc:
                error = PlaybookAgentOutputContractError(str(exc))
                error.raw_response = artifact.get("stdout", "")  # type: ignore[attr-defined]
                error.trajectory = trajectory  # type: ignore[attr-defined]
                raise error from exc
        finally:
            environment.cleanup()


def run_repository_checker(
    *,
    model_config: Mapping[str, Any],
    repository_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    repository: Mapping[str, Any],
    image_authority: Mapping[str, Any],
    attempt_dir: Path,
    issue: str,
    plan: str,
    checker_visible_playbook: str,
    retry_feedback: str = "",
    phase: str = "repo_checker",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _run_repository_json_agent(
        model_config=model_config,
        repository_config={**repository_config, "phase": phase},
        system=system,
        instance_template=instance_template,
        repository=repository,
        image_authority=image_authority,
        attempt_dir=attempt_dir,
        task="Review every listed concern against the proposed Plan.",
        artifact_name="repo_checker.json",
        prompt_values={
            "issue": issue,
            "plan": plan,
            "checker_visible_playbook": checker_visible_playbook,
            "retry_feedback": retry_feedback,
        },
        source_access_issue=issue,
    )


def run_repository_reflector(
    *,
    model_config: Mapping[str, Any],
    repository_config: Mapping[str, Any],
    system: str,
    instance_template: str,
    repository: Mapping[str, Any],
    image_authority: Mapping[str, Any],
    attempt_dir: Path,
    evidence_dir: str,
    internal_playbook: str,
    source_access_issue: str,
    pair_instance_id: str | None = None,
    retry_feedback: str = "",
    task: str = "Attribute this completed case to every active concern.",
    phase: str = "repo_reflector",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prompt_values = {
        "evidence_path": "/evidence",
        "internal_playbook": internal_playbook,
        "retry_feedback": retry_feedback,
    }
    if pair_instance_id is not None:
        prompt_values["pair_instance_id"] = pair_instance_id
    return _run_repository_json_agent(
        model_config=model_config,
        repository_config={**repository_config, "phase": phase},
        system=system,
        instance_template=instance_template,
        repository=repository,
        image_authority=image_authority,
        attempt_dir=attempt_dir,
        evidence_dir=evidence_dir,
        task=task,
        artifact_name="reflection.json",
        prompt_values=prompt_values,
        source_access_issue=source_access_issue,
    )
