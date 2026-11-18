"""Opt-in durable sessions over the unchanged shared Native transport.

PCE fingerprints its Native source. Keep that proven ephemeral execution path
unchanged; this adapter adds only session storage and explicit-ID recovery for
long-lived research owners. It does not implement authentication or a CLI loop.
"""
from contextlib import contextmanager
from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
import re
import tempfile

from src.agents.output_contract import AgentOutputContractError
from src.agents._deps import raise_for_permanent_provider_error
from src.optimization.codex_cli_runtime import (
    CodexConversation, CodexSIFExecution, CodexConfigurationError,
)
from src.optimization.hpc.task_batch import atomic_write


@dataclass(frozen=True)
class _SessionSIFExecution(CodexSIFExecution):
    session_root: Path | None = None

    @contextmanager
    def launch(self, model_config, environment, output_dir):
        (Path(environment["CODEX_HOME"]) / "sessions").mkdir()
        with super().launch(model_config, environment, output_dir) as (prefix, env, model, output):
            source = str(self.session_root.resolve())
            if any(char in source for char in (",", ":", "\n")):
                raise CodexConfigurationError("Native session mount contains an unsupported delimiter")
            index = prefix.index(str(self.sif_path.resolve()))
            prefix = [*prefix[:index], "--bind", f"{source}:/codex-state/sessions:rw", *prefix[index:]]
            yield prefix, env, model, output


class PersistentCodexConversation(CodexConversation):
    """Restore one main Native ID; keep auth in the base's temporary home."""
    def __init__(self, *, model_config, container, attempt_dir, session_dir, session_archive=None):
        self.session_dir = Path(session_dir)
        self.session_root = self.session_dir / "sessions"
        self.session_archive = session_archive
        if isinstance(container, CodexSIFExecution):
            container = _SessionSIFExecution(**{field.name: getattr(container, field.name)
                for field in fields(CodexSIFExecution)}, session_root=self.session_root)
        super().__init__(model_config=model_config, container=container, attempt_dir=attempt_dir)

    def __enter__(self):
        self.session_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.session_dir.chmod(0o700)
        if self.session_archive is not None and not any(self.session_root.rglob("*.jsonl")):
            restore_codex_sessions(self.session_root, self.session_archive)
        self.session_id = codex_session_id(self.session_root)
        return super().__enter__()

    def _checked_call(self, method, kwargs):
        expected = self.session_id
        try:
            result, trajectory = getattr(super(), method)(**kwargs)
        except Exception as exc:
            # The base's first-turn ID check can precede provider classification.
            # Preserve confirmed auth/credit failures instead of retrying format.
            for item in getattr(exc, "trajectory", []):
                if item.get("role") == "codex_cli":
                    content = item["content"]
                    raise_for_permanent_provider_error("CodexCLIError",
                        content.get("stderr", "") + json.dumps(content.get("events", [])))
            raise
        observed = next((event.get("thread_id") for item in trajectory
            if item.get("role") == "codex_cli" for event in item["content"]["events"]
            if event.get("type") == "thread.started"), None)
        if not observed or observed != (expected or self.session_id):
            error = AgentOutputContractError("Native resume did not retain its original session ID")
            error.trajectory = trajectory
            raise error
        return result, trajectory

    def call_text(self, **kwargs):
        return self._checked_call("call_text", kwargs)

    def call_json(self, **kwargs):
        return self._checked_call("call_json", kwargs)

    def session_evidence(self):
        return [{"path": str(path.relative_to(self.session_root)), "text": path.read_bytes().decode("utf-8")}
                for path in sorted(self.session_root.rglob("*.jsonl")) if path.is_file() and not path.is_symlink()]


def codex_session_id(root):
    """Select the sole main exec rollout, never a latest/unrelated subthread."""
    identifiers = set()
    for path in Path(root).rglob("*.jsonl"):
        if path.is_symlink() or not path.is_file():
            raise CodexConfigurationError("Native rollout must be a regular file")
        with path.open(encoding="utf-8") as stream:
            first = json.loads(next(stream))
        meta = first.get("payload", {})
        if first.get("type") != "session_meta":
            raise CodexConfigurationError("Native rollout lacks session_meta")
        if meta.get("source") == "exec" and meta.get("thread_source", "user") == "user":
            identifier = meta.get("id") or meta.get("session_id")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9-]+", identifier):
                raise CodexConfigurationError("Native main rollout lacks a valid session ID")
            identifiers.add(identifier)
    if len(identifiers) > 1:
        raise CodexConfigurationError("Multiple main Native sessions; select the original run authority")
    return next(iter(identifiers), None)


def restore_codex_sessions(root, archive):
    """Atomically import original JSONL bytes; never import auth or config."""
    root = Path(root)
    rows = json.loads(Path(archive).read_text(encoding="utf-8"))
    if not rows:
        return  # No inference happened in that owner; no conversation to restore.
    for row in rows:
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".jsonl":
            raise CodexConfigurationError("Native archive has an invalid rollout path")
        path = root / relative
        if path.exists() and path.read_bytes() != row["text"].encode("utf-8"):
            raise CodexConfigurationError("Native archive would replace an existing rollout")
    if any(root.rglob("*.jsonl")):
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".native-import-", dir=root.parent) as temporary:
        staged = Path(temporary) / "sessions"
        for row in rows:
            raw = row["text"].encode("utf-8")
            atomic_write(staged / row["path"], lambda path, raw=raw: path.write_bytes(raw))
        if not codex_session_id(staged):
            raise CodexConfigurationError("Native archive has no main exec session")
        os.replace(staged, root)
