"""Frozen authentication-only fixture from successful SWE-chat formal8 high.

Source: 63a799f6ff6ddc767e4fb80636b9bbd324c1b84c,
src/optimization/codex_cli_runtime.py. The two functions below are AST-checked
against that commit. Never import this fixture from production Agent code.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterator, Mapping


class CodexCLIError(RuntimeError):
    """Codex CLI ended without a usable final response."""


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
