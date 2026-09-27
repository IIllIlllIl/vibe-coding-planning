"""Native Codex CLI runtime for playbook Reflector and Curator workers."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
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


@dataclass(frozen=True)
class CodexSIFExecution:
    """Only the current role's inputs are mounted into its SIF session."""

    sif_path: Path
    evidence_dir: Path
    repository_dir: Path | None = None
    masked_paths: tuple[str, ...] = ("/opt/miniconda3/pkgs",)

    @property
    def cwd(self) -> Path:
        return Path("/testbed" if self.repository_dir is not None else "/evidence")

    @contextmanager
    def launch(self, model_config, environment, output_dir):
        sif = self.sif_path.resolve(strict=True)
        binary_raw = _codex_binary(model_config)
        binary = Path(shutil.which(binary_raw) or binary_raw).resolve(strict=True)
        evidence = self.evidence_dir.resolve(strict=True)
        if not sif.is_file() or not binary.is_file() or not evidence.is_dir():
            raise CodexCLIError("Codex SIF, executable, or evidence is missing")
        # Evidence bundles are generated regular files. Never mount a symlink
        # that can resolve to unrelated input under another visible mount.
        if any(path.is_symlink() for path in evidence.rglob("*")):
            raise CodexCLIError("Codex evidence bundle contains a symbolic link")
        with tempfile.TemporaryDirectory(prefix="vibe-codex-sif-") as temporary:
            root = Path(temporary)
            for name in ("tmp", "home", "output", "mask"):
                (root / name).mkdir(mode=0o700)
            prefix = [
                "apptainer", "exec", "--cleanenv", "--containall", "--no-home",
                "--no-mount", "hostfs,bind-paths,cwd", "--pwd", str(self.cwd),
                "--env", "HOME=/agent-home,CODEX_HOME=/codex-state,TMPDIR=/tmp",
            ]
            mounts = [
                (binary.parent, "/opt/vibe-codex/bin", "ro"),
                (evidence, "/evidence", "ro"),
                (Path(environment["CODEX_HOME"]), "/codex-state", "rw"),
                (root / "home", "/agent-home", "rw"),
                (root / "tmp", "/tmp", "rw"),
                (root / "tmp", "/var/tmp", "rw"),
                (root / "output", "/agent-output", "rw"),
            ]
            if self.repository_dir is not None:
                mounts.append((self.repository_dir.resolve(strict=True), "/testbed", "ro"))
            for target in self.masked_paths:
                if not target.startswith("/opt/") or ".." in Path(target).parts:
                    raise ValueError("Codex masked paths must be under /opt")
                mounts.append((root / "mask", target, "ro"))
            for source, target, mode in mounts:
                if any(char in str(source) for char in (",", ":", "\n")):
                    raise ValueError("Codex mount source contains an unsupported delimiter")
                prefix.extend(["--bind", f"{source}:{target}:{mode}"])
            prefix.append(str(sif))
            # --cleanenv does not neutralize launcher control variables. Do
            # not inherit extra host binds or environment injection overrides.
            launch_env = {
                key: value for key, value in environment.items()
                if not key.startswith(("APPTAINER", "SINGULARITY"))
            }
            container_model = dict(model_config)
            container_model["codex_binary"] = "/opt/vibe-codex/bin/" + binary.name
            try:
                yield prefix, launch_env, container_model, root / "output"
            finally:
                raw_output = root / "output" / "codex_final_response.json"
                if raw_output.is_symlink():
                    raise CodexCLIError("Codex final response must be a regular file, not a symlink")
                if raw_output.is_file():
                    # Copy bytes verbatim, never repair scientific output.
                    shutil.copyfile(raw_output, output_dir / raw_output.name)


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
    command_prefix: list[str] | None = None,
    hidden_host_paths: tuple[Path, ...] = (),
) -> list[dict[str, Any]]:
    """Fail before inference when the pinned CLI or its sandbox is unusable."""
    binary = _codex_binary(model_config)
    checks: list[dict[str, Any]] = []
    version = subprocess.run(
        [*(command_prefix or []), binary, "--version"],
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
            *(command_prefix or []),
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
    if hidden_host_paths:
        # Test actual visibility inside the same container, without a model
        # call or writes to scientific inputs. Paths are argv, not shell text.
        isolation = subprocess.run(
            [
                *(command_prefix or []), "/bin/sh", "-c",
                'test -r /evidence/manifest.json || exit 10; '
                'for candidate in "$@"; do '
                'if test -e "$candidate"; then '
                'printf "Unexpected host path visible: %s\\n" "$candidate"; exit 11; '
                'fi; done; printf "SIF inputs visible; host paths hidden\\n"',
                "sif-isolation-probe",
                *(str(path.resolve()) for path in hidden_host_paths),
            ],
            text=True, capture_output=True, check=False, env=dict(environment),
        )
        checks.append({
            "check": "task_scoped_sif_visibility", "returncode": isolation.returncode,
            "stdout": isolation.stdout, "stderr": isolation.stderr,
        })
        if isolation.returncode != 0:
            error = CodexCLIError("Codex task-scoped SIF visibility preflight failed")
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


def run_codex_json_agent(*, container: CodexSIFExecution, **kwargs):
    """Production role entry point: a SIF boundary is mandatory, never fallback."""
    if not isinstance(container, CodexSIFExecution):
        raise ValueError("Codex playbook Agents require task-scoped SIF execution")
    return _run_codex_json_agent(container=container, **kwargs)


def _run_codex_json_agent(
    *,
    model_config: Mapping[str, Any],
    working_directory: Path,
    attempt_dir: Path,
    system: str,
    user: str,
    task: str,
    evidence_manifest_path: Path | None = None,
    container: CodexSIFExecution | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run one ephemeral read-only Codex session and parse its final JSON.

    The prompt is passed on stdin so task contents never enter the process
    command line. Slurm owns the complete session deadline; this function does
    not add a second wall-clock timeout.
    """
    if not working_directory.is_dir():
        raise ValueError("Codex working directory is missing")
    manifest_record: dict[str, Any] | None = None
    if evidence_manifest_path is not None:
        if not evidence_manifest_path.is_file():
            raise ValueError("Codex evidence manifest is missing")
        manifest_record = {
            "manifest": str(evidence_manifest_path.resolve()),
            "sha256": hashlib.sha256(evidence_manifest_path.read_bytes()).hexdigest(),
            "computed_by": "host",
            "proves_agent_read": False,
        }
    attempt_dir.mkdir(parents=True, exist_ok=True)
    output_path = attempt_dir / "codex_final_response.json"
    prompt = (
        f"{system.strip()}\n\n"
        f"Task:\n{task.strip()}\n\n"
        f"Input:\n{user.strip()}\n"
    )
    with _isolated_codex_environment(model_config) as environment:
        # Bare transport remains available for isolated CLI unit tests. All
        # playbook role dispatchers must supply the SIF execution boundary.
        transport = (
            container.launch(model_config, environment, attempt_dir)
            if container is not None
            else nullcontext(([], environment, model_config, attempt_dir))
        )
        with transport as (prefix, launch_env, effective_model, _output_dir):
            effective_cwd = container.cwd if container is not None else working_directory
            effective_output = (
                Path("/agent-output/codex_final_response.json")
                if container is not None else output_path
            )
            preflight_checks = _runtime_preflight(
                effective_model, working_directory=effective_cwd,
                environment=launch_env, command_prefix=prefix,
                hidden_host_paths=(
                    (container.evidence_dir, container.evidence_dir.parent, attempt_dir)
                    if container is not None else ()
                ),
            )
            command = [*prefix, *_codex_command(
                effective_model, working_directory=effective_cwd,
                output_path=effective_output,
            )]
            completed = subprocess.run(
                command, input=prompt, text=True, capture_output=True,
                check=False, env=launch_env,
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
                "effective_prompt": prompt,
                "execution_boundary": "task_scoped_sif" if container else "direct_cli",
            },
        },
    ]
    if manifest_record is not None:
        trajectory.append({"role": "host_evidence_manifest", "content": manifest_record})
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
