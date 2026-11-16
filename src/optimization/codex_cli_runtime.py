"""Shared Native Codex transport: read-only JSON roles and opt-in PCE Agents."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterator, Mapping

from src.agents.output_contract import AgentOutputContractError, json_object
from src.agents.runtime import AgentConfigurationError, REASONING_EFFORTS
from src.agents._deps import raise_for_permanent_provider_error
from src.exceptions import PermanentProviderError
from src.optimization.audit import JsonlLogger, optional_diagnostic


class CodexCLIError(RuntimeError):
    """Codex CLI ended without a usable final response."""


class CodexConfigurationError(CodexCLIError, AgentConfigurationError):
    """A pinned CLI/configuration mismatch; preserve the existing retry hook."""


@dataclass(frozen=True)
class CodexSIFExecution:
    """Only the current role's inputs are mounted into its SIF session."""

    sif_path: Path
    evidence_dir: Path
    repository_dir: Path | None = None
    masked_paths: tuple[str, ...] = ("/opt/miniconda3/pkgs",)
    interpreter: str | None = None
    repository_writable: bool = False
    repository_cwd: str = "/testbed"
    output_files: tuple[str, ...] = ("codex_final_response.json",)
    source_policy: dict[str, Any] | None = None
    tool_network_disabled: bool | None = None

    @property
    def cwd(self) -> Path:
        return Path(self.repository_cwd if self.repository_dir is not None else "/evidence")

    def _image_interpreter(self, prefix, environment, output_dir):
        """Resolve only inside the frozen image, with the production mounts.

        Preserve the historical interpreter when present. Other images may
        expose Python through their own PATH; never resolve it on the Host or
        install a replacement. Explicit caller overrides do not fall back.
        """
        preferred = self.interpreter or ("/opt/miniconda3/envs/testbed/bin/python"
                     if self.repository_dir is not None else "/usr/local/bin/python3")
        command = [*prefix, "/bin/sh", "-c",
                   'if [ -x "$1" ] || [ "$2" = explicit ]; then selected="$1"; '
                   'else selected=python3; fi; '
                   'exec "$selected" -c "import ctypes, sys; print(sys.executable)"',
                   "sif-python-probe", preferred, "explicit" if self.interpreter else "default"]
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, check=False, timeout=30)
        observed = result.stdout.strip()
        record = {"check": "sif_interpreter", "preferred": preferred,
                  "returncode": result.returncode, "stdout": result.stdout,
                  "stderr": result.stderr}
        JsonlLogger(output_dir / "audit_events.jsonl").write_optional("sif_interpreter_probe", **record)
        if result.returncode or not observed.startswith("/") or any(c in observed for c in "\n\r\x00"):
            error = CodexConfigurationError(
                f"SIF Python interpreter probe failed (exit {result.returncode}): "
                f"{result.stderr.strip() or repr(observed)}")
            error.trajectory = [{"role": "host_codex_preflight", "content": {"checks": [record]}}]
            raise error
        return observed

    @contextmanager
    def launch(self, model_config, environment, output_dir):
        if not re.fullmatch(r"/testbed[0-9]*", self.repository_cwd):
            raise ValueError("Codex repository cwd must be a task-local /testbed directory")
        if self.repository_writable and self.repository_dir is None:
            raise ValueError("Writable Codex execution requires a phase-local repository")
        if any(Path(name).name != name or name in {"", ".", ".."} for name in self.output_files):
            raise ValueError("Codex output filenames must be simple names")
        sif = self.sif_path.resolve(strict=True)
        binary_raw = _codex_binary(model_config)
        binary = Path(shutil.which(binary_raw) or binary_raw).resolve(strict=True)
        evidence = self.evidence_dir.resolve(strict=True)
        if not sif.is_file() or not binary.is_file() or not evidence.is_dir():
            raise CodexCLIError("Codex SIF, executable, or evidence is missing")
        # The pinned standalone release keeps bwrap beside bin/, not in it.
        # Preserve that layout inside the SIF without exposing the install's
        # mutable state, user configuration, or unrelated release directories.
        resources = binary.parent.parent / "codex-resources"
        if not resources.is_dir() or not os.access(resources / "bwrap", os.X_OK):
            raise CodexCLIError("Codex standalone release is missing executable codex-resources/bwrap")
        # Evidence bundles are generated regular files. Never mount a symlink
        # that can resolve to unrelated input under another visible mount.
        if any(path.is_symlink() for path in evidence.rglob("*")):
            raise CodexCLIError("Codex evidence bundle contains a symbolic link")
        with tempfile.TemporaryDirectory(prefix="vibe-codex-sif-", ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            for name in ("tmp", "home", "output", "mask"):
                (root / name).mkdir(mode=0o700)
            prefix = [
                "apptainer", "exec", "--cleanenv", "--containall",
                "--home", f"{root / 'home'}:/agent-home",
                "--no-mount", "hostfs,bind-paths,cwd", "--pwd", str(self.cwd),
                "--env", "CODEX_HOME=/codex-state,TMPDIR=/tmp",
            ]
            mounts = [
                (Path("/dev/full"), "/dev/full", "ro"),
                (Path(__file__).with_name("codex_private_mount.py"),
                 "/opt/vibe-codex/private_mount.py", "ro"),
                (binary.parent, "/opt/vibe-codex/bin", "ro"),
                (resources, "/opt/vibe-codex/codex-resources", "ro"),
                (evidence, "/evidence", "ro"),
                (Path(environment["CODEX_HOME"]), "/codex-state", "rw"),
                (root / "tmp", "/tmp", "rw"),
                (root / "tmp", "/var/tmp", "rw"),
                (root / "output", "/agent-output", "rw"),
            ]
            if self.source_policy is not None:
                from src.agents.codex_hooks import write_bundle, POLICY_DIR, AUDIT_DIR, AUDIT_FILE, SUBMISSION_AUDIT_FILE
                write_bundle(root / "policy", self.source_policy)
                (root / "policy-audit").mkdir(mode=0o700)
                mounts.extend([(root / "policy", POLICY_DIR, "ro"),
                               (root / "policy-audit", AUDIT_DIR, "rw")])
            if self.repository_dir is not None:
                mounts.append((self.repository_dir.resolve(strict=True), str(self.cwd),
                               "rw" if self.repository_writable else "ro"))
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
            interpreter = self._image_interpreter(prefix, launch_env, output_dir)
            prefix.extend([interpreter, "/opt/vibe-codex/private_mount.py"])
            container_model = dict(model_config)
            container_model["codex_binary"] = "/opt/vibe-codex/bin/" + binary.name
            try:
                yield prefix, launch_env, container_model, root / "output"
            finally:
                for name in self.output_files:
                    raw_output = root / "output" / name
                    if raw_output.is_symlink():
                        raise CodexCLIError("Codex final response must be a regular file, not a symlink")
                    if raw_output.is_file():
                        # Copy bytes verbatim, never repair scientific output.
                        shutil.copyfile(raw_output, output_dir / raw_output.name)
                if self.source_policy is not None:
                    names = [AUDIT_FILE]
                    if self.source_policy.get("submission_contract"):
                        names.append(SUBMISSION_AUDIT_FILE)
                    for name in names:
                        audit = root / "policy-audit" / name
                        with optional_diagnostic("native hook log copy"):
                            if audit.is_file() and not audit.is_symlink():
                                shutil.copyfile(audit, output_dir / name)


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
        raise CodexConfigurationError("Codex authentication authority is missing")
    return path


@contextmanager
def _isolated_codex_environment(
    model_config: Mapping[str, Any],
) -> Iterator[dict[str, str]]:
    """Give each call private volatile state and a transient auth snapshot.

    Restore the successful formal-run design: no shared lock, lease or auth
    write-back. This permits parallel calls but deliberately does not preserve
    CLI refreshes or make copied OAuth sessions refresh-independent. See the
    accepted temporary policy in docs/knowledge/codex-parallel-auth-20261024.md.
    """
    seed = _auth_bytes(_codex_auth_file(model_config))
    with tempfile.TemporaryDirectory(prefix="vibe-codex-home-", ignore_cleanup_errors=True) as raw_home:
        codex_home = Path(raw_home)
        codex_home.chmod(0o700)
        private_auth = codex_home / "auth.json"
        descriptor = os.open(private_auth, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(seed)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(codex_home)
        yield environment


def _auth_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise CodexConfigurationError("Codex authentication cache must be a regular file")
    raw = path.read_bytes()
    try:
        valid = isinstance(json.loads(raw), dict)
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise CodexConfigurationError("Codex authentication cache must contain a JSON object")
    return raw


def _runtime_preflight(
    model_config: Mapping[str, Any],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    command_prefix: list[str] | None = None,
    hidden_host_paths: tuple[Path, ...] = (),
    sif_isolation: bool = False,
    repository: bool = False,
    repository_writable: bool = False,
    tool_network_disabled: bool | None = None,
) -> list[dict[str, Any]]:
    """Fail before inference when the pinned CLI or selected boundary fails."""
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
            f"Codex CLI version probe exited with status {version.returncode}: {version.stderr.strip()}"
            if version.returncode != 0 else
            f"expected Codex CLI {expected}, observed {observed or 'unknown'}"
        )
        error = CodexConfigurationError(detail)
        error.trajectory = [  # type: ignore[attr-defined]
            {"role": "host_codex_preflight", "content": {"checks": checks}}
        ]
        raise error

    if sif_isolation:
        checks.append(_sif_isolation_preflight(command_prefix, environment, hidden_host_paths,
                                               repository=repository,
                                               repository_writable=repository_writable,
                                               repository_cwd=str(working_directory)))

    sandbox = subprocess.run(
        [
            *(command_prefix or []),
            binary,
            "sandbox",
            *_permission_options(working_directory, repository_writable, tool_network_disabled),
            "--permission-profile",
            _permission_profile(repository_writable, tool_network_disabled),
            "--cd",
            str(working_directory.resolve()),
            *(["/bin/sh", "-c", 'set -eu; probe=".vibe-codex-probe-$$"; '
               'index="$PWD/.git/$probe.index"; '
               'trap \'rm -f "$probe" "$index" "$index.lock"\' EXIT; '
               'touch "$probe"; '
               'GIT_INDEX_FILE="$index" git -c safe.directory="$PWD" read-tree HEAD; '
               'GIT_INDEX_FILE="$index" git -c safe.directory="$PWD" add --force -- "$probe"; '
               'test -n "$(GIT_INDEX_FILE="$index" git -c safe.directory="$PWD" diff --cached --name-only -- "$probe")"; '
               'touch /agent-output/.vibe-codex-probe-$$; rm /agent-output/.vibe-codex-probe-$$; '
               'if touch /evidence/.vibe-codex-probe-$$ 2>/dev/null; then exit 20; fi']
              if repository_writable else ["/bin/true"]),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=dict(environment),
    )
    checks.append(
        {
            "check": "workspace_write_sandbox" if repository_writable else "read_only_sandbox",
            "returncode": sandbox.returncode,
            "stdout": sandbox.stdout,
            "stderr": sandbox.stderr,
        }
    )
    if sandbox.returncode != 0:
        error_class = (CodexConfigurationError if any(fragment in sandbox.stderr.lower()
                       for fragment in ("unknown built-in profile", "unknown permission", "error parsing", "invalid configuration"))
                       else CodexCLIError)
        error = error_class(
            f"Codex {'workspace-write' if repository_writable else 'read-only'} sandbox preflight failed: "
            f"{sandbox.stderr.strip() or sandbox.stdout.strip()}"
        )
        error.trajectory = [  # type: ignore[attr-defined]
            {"role": "host_codex_preflight", "content": {"checks": checks}}
        ]
        raise error
    if tool_network_disabled:
        # A closed local port is not evidence of isolation: require the sandbox
        # to reject connect itself, not an ordinary connection-refused/timeout.
        interpreter = command_prefix[-2] if command_prefix else sys.executable
        script = ("import errno,socket,sys\n"
                  "try:\n"
                  " with socket.socket() as s:\n"
                  "  s.settimeout(3); s.connect(('127.0.0.1',9))\n"
                  "except OSError as e:\n"
                  " if e.errno not in (errno.EPERM,errno.EACCES): raise\n"
                  " print('TOOL_NETWORK_DENIED')\n"
                  "else: sys.exit('Tool network isolation was not enforced')\n")
        network = subprocess.run([*(command_prefix or []), binary, "sandbox",
            *_permission_options(working_directory, repository_writable, tool_network_disabled),
            "--permission-profile", _permission_profile(repository_writable, tool_network_disabled),
            "--cd", str(working_directory.resolve()), interpreter, "-c", script],
            text=True, capture_output=True, check=False, env=dict(environment), timeout=30)
        checks.append({"check": "tool_network_disabled", "returncode": network.returncode,
                       "stdout": network.stdout, "stderr": network.stderr})
        if network.returncode or "TOOL_NETWORK_DENIED" not in network.stdout:
            error = CodexConfigurationError("Codex tool network isolation preflight failed")
            error.trajectory = [{"role": "host_codex_preflight", "content": {"checks": checks}}]
            raise error
    if hidden_host_paths and not sif_isolation:
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


def _sif_isolation_preflight(command_prefix, environment, hidden_host_paths=(), *,
                             repository=False, repository_writable=False, repository_cwd="/testbed"):
    """Verify the outer boundary directly, without Codex or an inner sandbox.

    Probe only our disposable paths. Never read credentials or modify actual
    evidence: attempted creations use a unique filename and must be denied.
    """
    if not command_prefix or command_prefix[:2] != ["apptainer", "exec"]:
        raise ValueError("Outer isolation check requires an Apptainer command prefix")
    script = r'''
set -eu
test "$HOME" = /agent-home
test "$(id -u)" -ne 0
test "$(sed -n 's/^CapEff:[[:space:]]*//p' /proc/self/status)" = 0000000000000000
test -z "${VIBE_OUTER_HOST_SENTINEL+x}"
test -r /evidence/manifest.json
test -d /codex-state
test -d /agent-output
test -d /tmp
test -d /var/tmp
repository="$1"
writable="$2"
repository_cwd="$3"
shift 3
for candidate in "$@"; do
    if test -e "$candidate" || test -L "$candidate"; then
        printf 'Unexpected host path visible: %s\n' "$candidate"; exit 11
    fi
    for process in /proc/[0-9]*; do
        if test -e "$process/root$candidate"; then
            printf 'Host path visible through proc root\n'; exit 12
        fi
    done
done
for target in /evidence /opt/vibe-codex/bin /etc; do
    if touch "$target/.vibe-isolation-probe-$$" 2>/dev/null; then
        rm "$target/.vibe-isolation-probe-$$"
        printf 'Unexpected writable input/image mount: %s\n' "$target"; exit 13
    fi
done
if test "$repository" = 1; then
    test -d "$repository_cwd"
    if test "$writable" = 1; then
        touch "$repository_cwd/.vibe-isolation-probe-$$"
        rm "$repository_cwd/.vibe-isolation-probe-$$"
    elif touch "$repository_cwd/.vibe-isolation-probe-$$" 2>/dev/null; then
        rm "$repository_cwd/.vibe-isolation-probe-$$"; exit 14
    fi
fi
for target in /agent-home /codex-state /agent-output /tmp /var/tmp; do
    touch "$target/.vibe-isolation-probe-$$"
    rm "$target/.vibe-isolation-probe-$$"
done
printf 'Host/proc paths hidden; evidence and image read-only; repository writable=%s; private scratch writable\n' "$writable"
'''
    command = [*command_prefix, "/bin/sh", "-c", script, "sif-outer-isolation-probe",
               str(int(repository)), str(int(repository_writable)), repository_cwd,
               *(str(path.resolve()) for path in hidden_host_paths)]
    probe_environment = dict(environment)
    probe_environment["VIBE_OUTER_HOST_SENTINEL"] = "synthetic-not-a-credential"
    completed = subprocess.run(command, text=True, capture_output=True, check=False, env=probe_environment)
    record = {"check": "sif_outer_isolation", "returncode": completed.returncode,
              "stdout": completed.stdout, "stderr": completed.stderr}
    if completed.returncode:
        error = CodexCLIError("Codex outer SIF isolation preflight failed")
        error.trajectory = [{"role": "host_codex_preflight", "content": {"checks": [record]}}]
        raise error
    return record


def _workspace_options(working_directory: Path, *, tool_network_disabled: bool | None = None) -> list[str]:
    # Both sandbox preflight and inference select this exact named policy.
    # Workspace staging is required by the existing submission contract; only
    # this disposable repository's Git metadata receives the extra permission.
    cwd = json.dumps(str(working_directory.resolve()))
    profile = ('{extends=":workspace", workspace_roots={' + cwd + '=true}, '
               'filesystem={":workspace_roots"={"."="write", ".git"="write"}, '
               '"/agent-output"="write"}, network={enabled=' + str(not tool_network_disabled).lower() + '}}')
    return ["--config", 'default_permissions="vibe-workspace"',
            "--config", f"permissions.vibe-workspace={profile}"]


def _permission_profile(repository_writable: bool, tool_network_disabled: bool | None = None) -> str:
    return "vibe-workspace" if repository_writable else (
        ":read-only" if tool_network_disabled is None else "vibe-read-only")


def _permission_options(working_directory: Path, repository_writable: bool,
                        tool_network_disabled: bool | None = None) -> list[str]:
    """One policy for sandbox probes and inference; no legacy/profile mixing."""
    if tool_network_disabled is not None and type(tool_network_disabled) is not bool:
        raise CodexConfigurationError("tool_network_disabled must be boolean or omitted")
    if repository_writable:
        return _workspace_options(working_directory, tool_network_disabled=tool_network_disabled)
    if tool_network_disabled is None:
        return []
    profile = '{extends=":read-only",network={enabled=' + str(not tool_network_disabled).lower() + '}}'
    return ["--config", 'default_permissions="vibe-read-only"',
            "--config", "permissions.vibe-read-only=" + profile]


def _codex_command(
    model_config: Mapping[str, Any],
    *,
    working_directory: Path,
    output_path: Path,
    sif_isolation: bool = False,
    repository_writable: bool = False,
    command_hook_options: tuple[str, ...] = (),
    tool_network_disabled: bool | None = None,
    persistent: bool = False,
    session_id: str | None = None,
) -> list[str]:
    if model_config.get("executor") != "codex_cli":
        raise ValueError("Codex runtime requires executor: codex_cli")
    model = str(model_config.get("model", "")).strip()
    if not model:
        raise ValueError("Codex runtime requires a model")
    effort = str(model_config.get("reasoning_effort", "high")).strip()
    if effort not in REASONING_EFFORTS:
        raise ValueError("unsupported Codex reasoning_effort")
    binary = _codex_binary(model_config)
    return [
        binary,
        "exec",
        *([] if persistent else ["--ephemeral"]),
        "--ignore-user-config",
        "--ignore-rules",
        *command_hook_options,
        *(_permission_options(working_directory, repository_writable, tool_network_disabled)
          if repository_writable or tool_network_disabled is not None else ["--sandbox", "read-only"]),
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
        *(["--config", "features.multi_agent=true", "--config",
           f"agents.default_subagent_model={json.dumps(model)}", "--config",
           f"agents.default_subagent_reasoning_effort={json.dumps(effort)}"] if persistent else []),
        *(_source_tool_options() if repository_writable or command_hook_options or tool_network_disabled else []),
        "--cd",
        str(working_directory.resolve()),
        "--output-last-message",
        str(output_path.resolve()),
        *(["resume", session_id] if session_id else []),
        "-",
    ]


def _source_tool_options() -> list[str]:
    # Apps execute service-side and bypass the command sandbox's network
    # policy. Disable discovery as well as built-in web search for isolated
    # experiment roles; native shell/file/delegation tools remain available.
    return ["--config", 'web_search="disabled"',
            "--config", "features.apps=false",
            "--config", "apps._default.enabled=false"]


class CodexConversation:
    """One task-private, resumable Native conversation in the existing SIF.

    Authentication stays in its private temporary home. Only session JSONL
    evidence, never auth/config/database files, is exported to scientific traces.
    """
    def __init__(self, *, model_config, container, attempt_dir):
        self.model_config, self.container = model_config, container
        self.attempt_dir = Path(attempt_dir)
        self.session_id = None
        self._stack = ExitStack()

    def __enter__(self):
        self.attempt_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.environment = self._stack.enter_context(_isolated_codex_environment(self.model_config))
            self.transport = self._stack.enter_context(
                self.container.launch(self.model_config, self.environment, self.attempt_dir))
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, *exception):
        return self._stack.__exit__(*exception)

    def call_json(self, *, system, user, task, output_filename):
        return run_codex_json_agent(container=self.container, model_config=self.model_config,
            working_directory=self.container.evidence_dir, attempt_dir=self.attempt_dir,
            system=system, user=user, task=task, output_filename=output_filename,
            conversation=self)

    def call_text(self, *, system, user, task, output_filename):
        return run_codex_agent(container=self.container, model_config=self.model_config,
            working_directory=self.container.evidence_dir, attempt_dir=self.attempt_dir,
            system=system, user=user, task=task, output_filename=output_filename,
            conversation=self)

    def session_evidence(self):
        root = Path(self.environment["CODEX_HOME"]) / "sessions"
        return [{"path": str(path.relative_to(root)), "text": path.read_bytes().decode("utf-8")}
                for path in sorted(root.rglob("*.jsonl")) if path.is_file() and not path.is_symlink()]


def run_codex_json_agent(*, container: CodexSIFExecution, **kwargs):
    """Production role entry point: a SIF boundary is mandatory, never fallback."""
    if not isinstance(container, CodexSIFExecution):
        raise ValueError("Codex playbook Agents require task-scoped SIF execution")
    if container.repository_writable:
        raise ValueError("Codex learning/review JSON roles must remain read-only")
    return _run_codex_json_agent(container=container, **kwargs)


def _run_codex_json_agent(
    **kwargs,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw, trajectory = _run_codex_text_agent(**kwargs)
    try:
        output = json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        error = AgentOutputContractError(str(exc))
        error.raw_response = raw
        error.trajectory = trajectory
        raise error from exc
    return output, trajectory


def run_codex_text_agent(*, container: CodexSIFExecution, **kwargs):
    """Compatibility adapter for the raw text submission contract."""
    return run_codex_agent(container=container, output_filename="codex_final_response.txt", **kwargs)


def run_codex_agent(*, container: CodexSIFExecution, output_filename: str, **kwargs):
    """Raw Native transport; output format and repository access are independent."""
    if not isinstance(container, CodexSIFExecution):
        raise ValueError("Codex Agents require task-scoped SIF execution")
    if output_filename not in container.output_files:
        raise ValueError("Codex transport requires its raw final-response artifact")
    return _run_codex_text_agent(container=container, output_filename=output_filename, **kwargs)


def _run_codex_text_agent(
    *,
    model_config: Mapping[str, Any],
    working_directory: Path,
    attempt_dir: Path,
    system: str,
    user: str,
    task: str,
    evidence_manifest_path: Path | None = None,
    container: CodexSIFExecution | None = None,
    output_filename: str = "codex_final_response.json",
    conversation: CodexConversation | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a Codex turn and retain exact final text; ephemeral by default.

    Production sessions use the verified SIF boundary plus Codex's inner
    role-scoped sandbox. Neither silently falls back to Host execution.
    A supplied CodexConversation keeps only this task's private transport open
    across turns; its session ID never selects an unrelated previous chat.

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
    output_path = attempt_dir / output_filename
    prompt = (
        f"{system.strip()}\n\n"
        f"Task:\n{task.strip()}\n\n"
        f"Input:\n{user.strip()}\n"
    )
    completed = None
    teardown_error = None
    try:
        with (nullcontext(conversation.environment) if conversation is not None
              else _isolated_codex_environment(model_config)) as environment:
            # Bare transport remains available for isolated CLI unit tests. All
            # playbook role dispatchers must supply the SIF execution boundary.
            transport = (
                nullcontext(conversation.transport) if conversation is not None else
                container.launch(model_config, environment, attempt_dir)
                if container is not None
                else nullcontext(([], environment, model_config, attempt_dir))
            )
            with transport as (prefix, launch_env, effective_model, _output_dir):
                effective_cwd = container.cwd if container is not None else working_directory
                effective_output = (
                    Path("/agent-output") / output_filename
                    if container is not None else output_path
                )
                preflight_checks = _runtime_preflight(
                    effective_model, working_directory=effective_cwd,
                    environment=launch_env, command_prefix=prefix,
                    hidden_host_paths=(
                        (container.evidence_dir, container.evidence_dir.parent, attempt_dir,
                         Path.home(), Path.cwd())
                        if container is not None else ()
                    ),
                    sif_isolation=container is not None,
                    repository=container is not None and container.repository_dir is not None,
                    repository_writable=container is not None and container.repository_writable,
                    tool_network_disabled=container.tool_network_disabled if container is not None else None,
                )
                command_hooks = ()
                if container is not None and container.source_policy is not None:
                    from src.agents.codex_hooks import hook_options, POLICY_DIR
                    interpreter = prefix[-2]
                    command_hooks = tuple(hook_options(interpreter,
                        submission=bool(container.source_policy.get("submission_contract"))))
                    probe = subprocess.run([*prefix, interpreter, POLICY_DIR + "/policy.pyz", "--self-test"],
                        text=True, capture_output=True, check=False, env=launch_env, timeout=30)
                    preflight_checks.append({"check": "source_policy_bundle", "returncode": probe.returncode,
                                             "stdout": probe.stdout, "stderr": probe.stderr})
                    if probe.returncode != 0 or '"policy_probe": "passed"' not in probe.stdout:
                        raise CodexConfigurationError("Native source-policy bundle preflight failed")
                    # Parse the actual hook TOML with the pinned CLI, without inference.
                    features = subprocess.run([*prefix, _codex_binary(effective_model),
                        *command_hooks[:-1], *_source_tool_options(), "features", "list"],
                        text=True, capture_output=True, check=False, env=launch_env, timeout=30)
                    preflight_checks.append({"check": "source_policy_hook_config", "returncode": features.returncode,
                                             "stdout": features.stdout, "stderr": features.stderr})
                    if features.returncode or not re.search(r"(?m)^hooks\s+.*\btrue\s*$", features.stdout):
                        raise CodexConfigurationError("Pinned Codex did not enable source-policy hooks")
                command = [*prefix, *_codex_command(
                    effective_model, working_directory=effective_cwd,
                    output_path=effective_output,
                    sif_isolation=container is not None,
                    repository_writable=container is not None and container.repository_writable,
                    command_hook_options=command_hooks,
                    tool_network_disabled=container.tool_network_disabled if container is not None else None,
                    persistent=conversation is not None,
                    session_id=conversation.session_id if conversation is not None else None,
                )]
                completed = subprocess.run(
                    command, input=prompt, text=True, capture_output=True,
                    check=False, env=launch_env,
                )
                if conversation is not None:
                    # Preserve this exact response while the shared mount stays
                    # open for the subsequent Host-bookkeeping/Curator turn.
                    source = _output_dir / output_filename
                    if source.is_file() and not source.is_symlink():
                        shutil.copyfile(source, output_path)
    except Exception as exc:
        if completed is None:
            raise
        teardown_error = exc
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
                "ephemeral": conversation is None,
                "sandbox": "workspace-write" if container and container.repository_writable else "read-only",
                "inner_sandbox": True,
                "effective_prompt": prompt,
                **({"raw_stdout": completed.stdout} if output_filename == "codex_final_response.txt" else {}),
                "execution_boundary": "task_scoped_sif" if container else "direct_cli",
                "tool_network_disabled": container.tool_network_disabled if container else None,
            },
        },
    ]
    if conversation is not None and conversation.session_id is None:
        conversation.session_id = next((event.get("thread_id") for event in events
                                        if event.get("type") == "thread.started"), None)
        if not conversation.session_id:
            error = AgentOutputContractError("Persistent Codex response lacks a thread.started identity")
            error.trajectory = trajectory
            raise error
    if manifest_record is not None:
        trajectory.append({"role": "host_evidence_manifest", "content": manifest_record})
    if teardown_error is not None:
        if output_path.is_file() and not output_path.is_symlink():
            trajectory.append({"role": "assistant", "content": output_path.read_bytes().decode("utf-8")})
        teardown_error.trajectory = trajectory
        raise teardown_error
    native_errors = [event for event in events if event.get("type") in {"error", "turn.failed"}]
    if completed.returncode != 0 or native_errors:
        provider_error = completed.stderr + "\n" + json.dumps(native_errors)
        try:
            raise_for_permanent_provider_error("CodexCLIError", provider_error)
        except PermanentProviderError as error:
            error.trajectory = trajectory
            raise
    if completed.returncode != 0 or native_errors:
        error = CodexCLIError(
            f"Codex CLI turn failed (process status {completed.returncode}): "
            f"{completed.stderr.strip() or json.dumps(native_errors)}"
        )
        error.trajectory = trajectory  # type: ignore[attr-defined]
        raise error
    if not output_path.is_file():
        error = AgentOutputContractError("Codex CLI did not write its final response")
        error.trajectory = trajectory  # type: ignore[attr-defined]
        raise error
    raw = output_path.read_bytes().decode("utf-8")
    trajectory.append({"role": "assistant", "content": raw})
    return raw, trajectory
