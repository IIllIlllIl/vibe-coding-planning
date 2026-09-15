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
from src.environment.source_access import (
    SOURCE_ACCESS_POLICY_VERSION,
    extract_http_urls,
    summarize_source_access_log,
)
from src.evaluator.swe_evaluator import derive_image_name


class PlaybookAgentOutputContractError(ValueError):
    """An Agent returned a final artifact that Host parsing cannot accept."""


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if match:
        stripped = match.group(1)
    value = json.loads(stripped)
    if not isinstance(value, dict):
        raise ValueError("model output must be a JSON object")
    return value


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
        )

    def __call__(self, system: str, user: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        response = self.model.query(messages)
        assistant = {"role": "assistant", "content": response["content"], "extra": response.get("extra", {})}
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run a tool-using Reflector over a read-only, repository-free bundle."""
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the Curator over a file-backed cross-case reflection bundle."""
    return _run_evidence_json_agent(
        model_config=model_config,
        evidence_config=reflection_config,
        system=system,
        instance_template=instance_template,
        evidence_dir=evidence_dir,
        task="Curate durable rejection concerns from the completed reflections.",
        artifact_name="curator.json",
        prompt_values={
            "evidence_path": "/evidence",
            "counted_internal_playbook": counted_internal_playbook,
            "case_count": case_count,
            "retry_feedback": retry_feedback,
        },
    )


def _render(template: str, **values: Any) -> str:
    return Environment(undefined=StrictUndefined, autoescape=False).from_string(template).render(**values)


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
            "Repo Agent requires the Safe PCE conservative_blacklist_v3 "
            "source boundary"
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
            run_args.extend(
                ["--bind", f"{Path(evidence_dir).resolve()}:/evidence:ro"]
            )
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
                repository_config.get("masked_container_paths", ["/opt/miniconda3/pkgs"])
            ),
            run_args=run_args,
            source_access_prompt_urls=list(extract_http_urls(source_access_issue)),
            source_access_log_path=source_access_path,
            source_access_context={
                "instance_id": repository["instance_id"],
                "phase": repository_config.get("phase", "repo_checker"),
            },
        )
        try:
            baseline = restore_repository_to_base(
                environment,
                str(repository["base_commit"]),
                phase=str(repository_config.get("phase", "repo_checker")),
                evidence_dir=baseline_dir,
                timeout=timeout,
                prune_future_history=bool(
                    repository_config.get("prune_future_history", True)
                ),
            )
            agent = build_default_agent(
                DefaultAgent,
                model,
                environment,
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _run_repository_json_agent(
        model_config=model_config,
        repository_config={**repository_config, "phase": "repo_checker"},
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
    retry_feedback: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return _run_repository_json_agent(
        model_config=model_config,
        repository_config={**repository_config, "phase": "repo_reflector"},
        system=system,
        instance_template=instance_template,
        repository=repository,
        image_authority=image_authority,
        attempt_dir=attempt_dir,
        evidence_dir=evidence_dir,
        task="Attribute this completed case to every active concern.",
        artifact_name="reflection.json",
        prompt_values={
            "evidence_path": "/evidence",
            "internal_playbook": internal_playbook,
            "retry_feedback": retry_feedback,
        },
        source_access_issue=source_access_issue,
    )
