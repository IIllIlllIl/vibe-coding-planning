#!/usr/bin/env python3
"""Watch and resume ULHPC login-node SIF preheating in small batches."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tools.login_apptainer_sif_preheat import (  # noqa: E402
    DEFAULT_APPTAINER_BIN,
    DEFAULT_APPTAINER_CACHE_DIR,
    DEFAULT_APPTAINER_TMP_DIR,
    DEFAULT_ULHPC_CONFIG,
    _remote_existing_sifs,
    _ssh_config,
)
from scripts.tools.prepare_apptainer_sifs import _collect_images  # noqa: E402
from src.optimization.config import load_optimization_config  # noqa: E402

LOGIN_PREHEAT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "login_apptainer_sif_preheat.py"
DEFAULT_STATE_FILE = REPO_ROOT / "output" / ".login_sif_preheat_watchdog_state.json"
DEFAULT_LOG_DIR = REPO_ROOT / "output" / "login-preheat-logs"


@dataclass
class WatchdogState:
    phase: str = "idle"
    expected_images: int = 0
    last_sif_count: int = 0
    runs: int = 0
    no_progress_runs: int = 0
    last_returncode: int | None = None
    last_log_path: str | None = None
    last_error: str | None = None
    started_at: str | None = None
    updated_at: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path) -> WatchdogState:
    if not path.is_file():
        return WatchdogState()
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = asdict(WatchdogState())
    defaults.update(data)
    return WatchdogState(**defaults)


def save_state(path: Path, state: WatchdogState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = now_iso()
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repeatedly run login-node SIF preheat batches until the cache is complete.",
        allow_abbrev=False,
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--ulhpc-config", default=str(DEFAULT_ULHPC_CONFIG))
    parser.add_argument("--sif-cache-dir")
    parser.add_argument("--apptainer-bin", default=DEFAULT_APPTAINER_BIN)
    parser.add_argument("--apptainer-cache-dir", default=DEFAULT_APPTAINER_CACHE_DIR)
    parser.add_argument("--apptainer-tmp-dir", default=DEFAULT_APPTAINER_TMP_DIR)
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE))
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=21600)
    parser.add_argument("--max-attempts", type=int, default=1)
    parser.add_argument("--retry-backoff", type=int, default=0)
    parser.add_argument("--check-interval", type=int, default=3600)
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--max-no-progress-runs", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _sif_cache_dir(config_path: Path, override: str | None) -> tuple[str, int]:
    config = load_optimization_config(config_path, require_api_keys=False)
    if config.container.runtime != "apptainer":
        raise SystemExit(
            f"config container.runtime is {config.container.runtime!r}; expected 'apptainer'"
        )
    cache_dir = str(override or config.container.sif_cache_dir)
    if not cache_dir:
        raise SystemExit("--sif-cache-dir is required when config has no container.sif_cache_dir")
    return cache_dir, len(_collect_images(config))


def remote_sif_count(ulhpc_config: Path, sif_cache_dir: str) -> int:
    target, port, ssh_key = _ssh_config(ulhpc_config)
    return len(_remote_existing_sifs(target, port, ssh_key, sif_cache_dir))


def build_preheat_command(args: argparse.Namespace, config_path: Path, batch_size: int) -> list[str]:
    command = [
        sys.executable,
        str(LOGIN_PREHEAT_SCRIPT),
        "--config",
        str(config_path),
        "--ulhpc-config",
        str(args.ulhpc_config),
        "--apptainer-bin",
        args.apptainer_bin,
        "--apptainer-cache-dir",
        args.apptainer_cache_dir,
        "--apptainer-tmp-dir",
        args.apptainer_tmp_dir,
        "--missing-only",
        "--limit",
        str(batch_size),
        "--timeout",
        str(args.timeout),
        "--max-attempts",
        str(args.max_attempts),
        "--retry-backoff",
        str(args.retry_backoff),
    ]
    if args.sif_cache_dir:
        command.extend(["--sif-cache-dir", args.sif_cache_dir])
    return command


def run_batch(args: argparse.Namespace, config_path: Path, log_dir: Path, run_number: int) -> int:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"login-preheat-run-{run_number:04d}.log"
    command = build_preheat_command(args, config_path, args.batch_size)
    if args.dry_run:
        command.append("--dry-run")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(json.dumps({"event": "watchdog_command", "command": command}) + "\n")
        log.flush()
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return result.returncode


def validate_args(args: argparse.Namespace) -> None:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be >= 1")
    if args.retry_backoff < 0:
        raise SystemExit("--retry-backoff must be >= 0")
    if args.check_interval < 0:
        raise SystemExit("--check-interval must be >= 0")
    if args.max_runs < 0:
        raise SystemExit("--max-runs must be >= 0")
    if args.max_no_progress_runs < 1:
        raise SystemExit("--max-no-progress-runs must be >= 1")


def main() -> int:
    args = parse_args()
    validate_args(args)
    config_path = (REPO_ROOT / args.config).resolve()
    state_path = (REPO_ROOT / args.state_file).resolve()
    log_dir = (REPO_ROOT / args.log_dir).resolve()
    ulhpc_config = (REPO_ROOT / args.ulhpc_config).resolve()
    sif_cache_dir, expected = _sif_cache_dir(config_path, args.sif_cache_dir)

    state = load_state(state_path)
    if state.started_at is None:
        state.started_at = now_iso()
    state.expected_images = expected

    while args.max_runs == 0 or state.runs < args.max_runs:
        before = remote_sif_count(ulhpc_config, sif_cache_dir)
        state.last_sif_count = before
        state.phase = "completed" if before >= expected else "running"
        save_state(state_path, state)
        print(
            json.dumps(
                {
                    "event": "watchdog_status",
                    "sif_count": before,
                    "expected": expected,
                    "phase": state.phase,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if before >= expected:
            return 0

        state.runs += 1
        rc = run_batch(args, config_path, log_dir, state.runs)
        after = remote_sif_count(ulhpc_config, sif_cache_dir)
        state.last_returncode = rc
        state.last_sif_count = after
        state.last_log_path = str(log_dir / f"login-preheat-run-{state.runs:04d}.log")
        if after > before:
            state.no_progress_runs = 0
            state.last_error = None if rc == 0 else f"batch rc={rc}; cache still progressed"
        else:
            state.no_progress_runs += 1
            state.last_error = f"batch rc={rc}; no SIF cache progress"
        state.phase = "running"
        save_state(state_path, state)
        print(
            json.dumps(
                {
                    "event": "watchdog_batch_finished",
                    "run": state.runs,
                    "returncode": rc,
                    "before": before,
                    "after": after,
                    "no_progress_runs": state.no_progress_runs,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if state.no_progress_runs >= args.max_no_progress_runs:
            state.phase = "blocked"
            save_state(state_path, state)
            return 2
        if args.dry_run:
            return 0
        if args.check_interval > 0:
            time.sleep(args.check_interval)

    state.phase = "blocked"
    state.last_error = "max runs reached before cache completion"
    save_state(state_path, state)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
