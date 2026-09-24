"""Native Codex CLI runtime for playbook Reflector and Curator workers."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterator, Mapping

from src.optimization.playbook_runtime import (
    PlaybookAgentOutputContractError,
    _json_object,
)


class CodexCLIError(RuntimeError):
    """Codex CLI ended without a usable final response."""


class CodexEvidenceAccessError(CodexCLIError):
    """Codex completed without proving access to mandatory local evidence."""


def _codex_binary(model_config: Mapping[str, Any]) -> str:
    raw = str(model_config.get("codex_binary", "codex")).strip()
    if not raw:
        raise ValueError("Codex runtime requires a non-empty codex_binary")
    if "/" not in raw:
        return raw
    return str(Path(os.path.expandvars(raw)).expanduser())


def _codex_auth_file(model_config: Mapping[str, Any]) -> Path:
    raw = str(
        model_config.get("codex_auth_file", "${HOME}/.codex/auth.json")
    ).strip()
    if not raw:
        raise ValueError("Codex runtime requires a non-empty codex_auth_file")
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_file():
        raise CodexCLIError("Codex authentication authority is missing")
    return path


@contextmanager
def _isolated_codex_environment(
    model_config: Mapping[str, Any],
) -> Iterator[dict[str, str]]:
    """Give one Agent private volatile Codex state and a transient auth copy."""
    auth_source = _codex_auth_file(model_config)
    with tempfile.TemporaryDirectory(prefix="vibe-codex-home-") as raw_home:
        codex_home = Path(raw_home)
        codex_home.chmod(0o700)
        private_auth = codex_home / "auth.json"
        shutil.copyfile(auth_source, private_auth)
        private_auth.chmod(0o600)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(codex_home)
        yield environment


def _runtime_preflight(
    model_config: Mapping[str, Any],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Fail before inference when the pinned CLI or its sandbox is unusable."""
    binary = _codex_binary(model_config)
    checks: list[dict[str, Any]] = []
    version = subprocess.run(
        [binary, "--version"],
        text=True,
        capture_output=True,
        check=False,
        env=dict(environment),
    )
    checks.append(
        {
            "check": "version",
            "returncode": version.returncode,
            "stdout": version.stdout,
            "stderr": version.stderr,
        }
    )
    expected = str(model_config.get("codex_version", "")).strip()
    observed_match = re.search(r"\bcodex-cli\s+(\S+)", version.stdout)
    observed = observed_match.group(1) if observed_match else ""
    if version.returncode != 0 or (expected and observed != expected):
        detail = (
            f"expected Codex CLI {expected}, observed {observed or 'unknown'}"
            if expected
            else f"Codex CLI version probe exited with status {version.returncode}"
        )
        error = CodexCLIError(detail)
        error.trajectory = [  # type: ignore[attr-defined]
            {"role": "host_codex_preflight", "content": {"checks": checks}}
        ]
        raise error

    sandbox = subprocess.run(
        [
            binary,
            "sandbox",
            "--permission-profile",
            ":read-only",
            "--cd",
            str(working_directory.resolve()),
            "/bin/true",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=dict(environment),
    )
    checks.append(
        {
            "check": "read_only_sandbox",
            "returncode": sandbox.returncode,
            "stdout": sandbox.stdout,
            "stderr": sandbox.stderr,
        }
    )
    if sandbox.returncode != 0:
        error = CodexCLIError(
            "Codex read-only sandbox preflight failed: "
            f"{sandbox.stderr.strip() or sandbox.stdout.strip()}"
        )
        error.trajectory = [  # type: ignore[attr-defined]
            {"role": "host_codex_preflight", "content": {"checks": checks}}
        ]
        raise error
    return checks


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
    binary = _codex_binary(model_config)
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
    evidence_manifest_path: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one ephemeral read-only Codex session and parse its final JSON.

    The prompt is passed on stdin so task contents never enter the process
    command line. Slurm owns the complete session deadline; this function does
    not add a second wall-clock timeout.
    """
    if not working_directory.is_dir():
        raise ValueError("Codex working directory is missing")
    expected_receipt: str | None = None
    receipt_instruction = ""
    if evidence_manifest_path is not None:
        if not evidence_manifest_path.is_file():
            raise ValueError("Codex evidence manifest is missing")
        expected_receipt = hashlib.sha256(evidence_manifest_path.read_bytes()).hexdigest()
        receipt_instruction = (
            "\n\nOperational evidence receipt:\n"
            f"Before analysis, read {evidence_manifest_path.resolve()}. Compute the "
            "lowercase SHA-256 of the exact file bytes and include it in the top-level "
            'JSON field "evidence_receipt_sha256". This field proves local evidence '
            "access and is removed by the Host before scientific validation."
        )
    attempt_dir.mkdir(parents=True, exist_ok=True)
    output_path = attempt_dir / "codex_final_response.json"
    prompt = (
        f"{system.strip()}\n\n"
        f"Task:\n{task.strip()}{receipt_instruction}\n\n"
        f"Input:\n{user.strip()}\n"
    )
    with _isolated_codex_environment(model_config) as environment:
        preflight_checks = _runtime_preflight(
            model_config,
            working_directory=working_directory,
            environment=environment,
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
            env=environment,
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
            "role": "host_codex_preflight",
            "content": {"checks": preflight_checks},
        },
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
    if expected_receipt is not None:
        observed_receipt = output.pop("evidence_receipt_sha256", None)
        receipt_record = {
            "manifest": str(evidence_manifest_path.resolve()),
            "expected_sha256": expected_receipt,
            "observed_sha256": observed_receipt,
            "matched": observed_receipt == expected_receipt,
        }
        trajectory.append(
            {"role": "host_evidence_receipt", "content": receipt_record}
        )
        if observed_receipt != expected_receipt:
            error = CodexEvidenceAccessError(
                "Codex did not return the exact mandatory evidence receipt"
            )
            error.trajectory = trajectory  # type: ignore[attr-defined]
            raise error
    return output, trajectory
