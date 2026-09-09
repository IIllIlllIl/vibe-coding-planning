"""Single-call prompt runtime used inside reject-playbook workers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from jinja2 import Environment, StrictUndefined

from src.agents._deps import (
    build_default_agent,
    build_model,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow


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
    cache = Path(str(reflection_config["evidence_sif_cache_dir"]))
    capacity = DockerCapacityWindow(
        max_concurrent=1,
        max_cached_images=1,
        min_free_gb=1,
        disk_path=cache,
        enable_docker_maintenance=False,
    )
    environment = ApptainerEnvironment(
        image=str(reflection_config.get("evidence_image", "python:3.12-slim")),
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
        timeout=int(reflection_config.get("command_timeout_seconds", 120)),
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
            step_limit=int(reflection_config.get("max_steps", 20)),
        )
        exit_status, submission = agent.run(
            task="Attribute this case to every active rejection rule.",
            evidence_path="/evidence",
            internal_playbook=internal_playbook,
            retry_feedback=retry_feedback,
        )
        raise_for_permanent_provider_error(exit_status, submission)
        if exit_status != "Submitted":
            error = RuntimeError(
                "Reflector ended without a submitted artifact "
                f"(exit_status={exit_status})"
            )
            error.trajectory = list(agent.messages)  # type: ignore[attr-defined]
            raise error
        # The submitted terminal observation may contain container diagnostics.
        # Treat the Agent-authored file as the data authority and read only its
        # stdout; stderr remains separate diagnostic evidence.
        artifact = environment.execute(
            "cat /tmp/reflection.json",
            cwd="/evidence",
            timeout=int(reflection_config.get("command_timeout_seconds", 120)),
        )
        trajectory = [
            *list(agent.messages),
            {
                "role": "host_artifact_read",
                "content": {
                    "path": "/tmp/reflection.json",
                    "returncode": artifact["returncode"],
                    "stderr": artifact.get("stderr", ""),
                    "terminal_submission": submission,
                },
            },
        ]
        if artifact["returncode"] != 0:
            error = RuntimeError("Reflector artifact could not be read")
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


def _render(template: str, **values: Any) -> str:
    return Environment(undefined=StrictUndefined, autoescape=False).from_string(template).render(**values)
