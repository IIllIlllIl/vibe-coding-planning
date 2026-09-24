"""Native Codex CLI runtime for playbook Reflector and Curator workers."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

from src.optimization.playbook_runtime import (
    PlaybookAgentOutputContractError,
    _json_object,
)


class CodexCLIError(RuntimeError):
    """Codex CLI ended without a usable final response."""


def _codex_command(
    model_config: Mapping[str, Any],
    *,
    working_directory: Path,
    output_path: Path,
) -> list[str]:
    if model_config.get("executor") != "codex_cli":
        raise ValueError("Codex runtime requires executor: codex_cli")
    model = str(model_config.get("model", "")).strip()
    if not model:
        raise ValueError("Codex runtime requires a model")
    effort = str(model_config.get("reasoning_effort", "high")).strip()
    if effort not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        raise ValueError("unsupported Codex reasoning_effort")
    binary = str(model_config.get("codex_binary", "codex")).strip()
    if not binary:
        raise ValueError("Codex runtime requires a non-empty codex_binary")
    return [
        binary,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--json",
        "--color",
        "never",
        "--model",
        model,
        "--config",
        f"model_reasoning_effort={json.dumps(effort)}",
        "--config",
        "project_doc_max_bytes=0",
        "--cd",
        str(working_directory.resolve()),
        "--output-last-message",
        str(output_path.resolve()),
        "-",
    ]


def run_codex_json_agent(
    *,
    model_config: Mapping[str, Any],
    working_directory: Path,
    attempt_dir: Path,
    system: str,
    user: str,
    task: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one ephemeral read-only Codex session and parse its final JSON.

    The prompt is passed on stdin so task contents never enter the process
    command line. Slurm owns the complete session deadline; this function does
    not add a second wall-clock timeout.
    """
    if not working_directory.is_dir():
        raise ValueError("Codex working directory is missing")
    attempt_dir.mkdir(parents=True, exist_ok=True)
    output_path = attempt_dir / "codex_final_response.json"
    prompt = (
        f"{system.strip()}\n\n"
        f"Task:\n{task.strip()}\n\n"
        f"Input:\n{user.strip()}\n"
    )
    command = _codex_command(
        model_config,
        working_directory=working_directory,
        output_path=output_path,
    )
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    events: list[dict[str, Any]] = []
    malformed_events: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed_events.append(line)
            continue
        events.append(event if isinstance(event, dict) else {"value": event})
    trajectory = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {
            "role": "codex_cli",
            "content": {
                "returncode": completed.returncode,
                "events": events,
                "malformed_event_lines": malformed_events,
                "stderr": completed.stderr,
                "ephemeral": True,
                "sandbox": "read-only",
            },
        },
    ]
    if completed.returncode != 0:
        error = CodexCLIError(
            f"Codex CLI exited with status {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
        error.trajectory = trajectory  # type: ignore[attr-defined]
        raise error
    if not output_path.is_file():
        error = CodexCLIError("Codex CLI did not write its final response")
        error.trajectory = trajectory  # type: ignore[attr-defined]
        raise error
    raw = output_path.read_text(encoding="utf-8")
    try:
        output = _json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        error = PlaybookAgentOutputContractError(str(exc))
        error.raw_response = raw  # type: ignore[attr-defined]
        error.trajectory = trajectory  # type: ignore[attr-defined]
        raise error from exc
    trajectory.append({"role": "assistant", "content": raw})
    return output, trajectory
