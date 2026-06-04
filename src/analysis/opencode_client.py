"""OpenCode command-line client for analysis backends."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import AnalysisConfig
from src.exceptions import TaskError

logger = logging.getLogger(__name__)


_RATE_LIMIT_PATTERNS = (
    "429",
    "rate limit",
    "ratelimit",
    "too many requests",
    "quota",
    "try again later",
    "retry later",
    "retryable",
)

_PROCESS_DATA_HOME: Path | None = None


@dataclass(frozen=True)
class OpenCodeResult:
    """Result from one OpenCode invocation."""

    stdout: str
    stderr: str
    xdg_data_home: str


def is_rate_limit_error(text: str) -> bool:
    """Return True when stderr/stdout appears to be a provider quota error."""
    lowered = text.lower()
    return any(pattern in lowered for pattern in _RATE_LIMIT_PATTERNS)


def prepare_xdg_data_home(config: AnalysisConfig) -> Path:
    """Create or reuse an isolated OpenCode data directory.

    OpenCode stores auth and sqlite state under XDG_DATA_HOME/opencode. Using an
    isolated directory avoids failures from a corrupt global sqlite/WAL state.
    If the isolated auth file is missing, copy the user's existing auth.json.
    """
    global _PROCESS_DATA_HOME

    if config.opencode_xdg_data_home:
        data_home = Path(config.opencode_xdg_data_home).expanduser()
    elif config.opencode_isolate_per_case:
        data_home = Path(tempfile.mkdtemp(prefix="opencode-analysis-"))
    else:
        if _PROCESS_DATA_HOME is None:
            _PROCESS_DATA_HOME = Path(tempfile.mkdtemp(prefix="opencode-analysis-"))
        data_home = _PROCESS_DATA_HOME

    opencode_dir = data_home / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    source_auth = Path.home() / ".local/share/opencode/auth.json"
    target_auth = opencode_dir / "auth.json"
    if source_auth.exists() and not target_auth.exists():
        shutil.copy2(source_auth, target_auth)

    return data_home


def run_opencode(
    *,
    config: AnalysisConfig,
    prompt: str,
    cwd: Path,
    files: list[Path] | None = None,
    xdg_data_home: Path | None = None,
    sleep_func=time.sleep,
) -> OpenCodeResult:
    """Run ``opencode run`` with retry handling for provider rate limits."""
    data_home = xdg_data_home or prepare_xdg_data_home(config)
    attempts = config.max_retries + 1
    last_detail = ""

    for attempt in range(1, attempts + 1):
        cmd = [
            config.opencode_bin,
            "run",
            "--pure",
            "--model",
            config.model,
            "--dir",
            str(cwd),
            prompt,
        ]
        # OpenCode's yargs array option can consume following positionals when
        # --file appears before the prompt. Keep file arguments after prompt.
        for file_path in files or []:
            cmd.append(f"--file={file_path}")

        env = os.environ.copy()
        env["XDG_DATA_HOME"] = str(data_home)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.opencode_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            detail = f"opencode timed out after {config.opencode_timeout}s"
            if exc.stderr:
                detail += f": {exc.stderr}"
            last_detail = detail
            if attempt < attempts:
                logger.warning(
                    "OpenCode/Kimi timed out on attempt %d/%d; sleeping %ss",
                    attempt,
                    attempts,
                    config.rate_limit_sleep_seconds,
                )
                sleep_func(config.rate_limit_sleep_seconds)
                continue
            raise TaskError(detail) from exc

        if result.returncode == 0:
            return OpenCodeResult(
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                xdg_data_home=str(data_home),
            )

        last_detail = (result.stderr.strip() or result.stdout.strip()).strip()
        if attempt < attempts and is_rate_limit_error(last_detail):
            logger.warning(
                "OpenCode/Kimi appears rate-limited on attempt %d/%d; sleeping %ss",
                attempt,
                attempts,
                config.rate_limit_sleep_seconds,
            )
            sleep_func(config.rate_limit_sleep_seconds)
            continue

        break

    raise TaskError(f"opencode failed after {attempt} attempt(s): {last_detail}")
