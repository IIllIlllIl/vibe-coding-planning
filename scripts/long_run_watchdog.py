#!/usr/bin/env python3
"""Long-run watchdog for PCT pipeline batch execution.

Runs for days unattended. Monitors the batch process, handles recovery,
and invokes Claude Code CLI for complex code repairs.

Usage:
    export DEEPSEEK_API_KEY="..."
    export ANTHROPIC_API_KEY="..."   # only needed if claude repair is triggered
    caffeinate -i -s -d python scripts/long_run_watchdog.py

State file (auto-created):
    output/.watchdog_state.json

Logs:
    logs/watchdog.log
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------
CHECK_INTERVAL_SECONDS = 3600         # 1 hour between health checks
HANG_TIMEOUT_SECONDS = 3 * 3600       # 3 hours with no log change = hung
API_COOLDOWN_SECONDS = 5 * 3600       # 5 hours for DeepSeek rate-limit recovery
DOCKER_RETRY_INTERVAL = 300           # 5 min between Docker daemon retries
DOCKER_MAX_RETRIES = 12               # 12 × 5 min = 60 min, then fatal
REPAIR_CHECK_INTERVAL = 60            # 1 min while waiting for Claude repair
DISK_MIN_FREE_GB = 10                 # Pause if less than 10 GB free

BATCH_TMUX_SESSION = "pct-batch"
REPAIR_TMUX_SESSION = "pct-repair"
MASTER_LOG = Path("logs/batch_run.log")
WATCHDOG_LOG = Path("logs/watchdog.log")
STATE_FILE = Path("output/.watchdog_state.json")

# Patterns that indicate specific failure modes
API_RATE_LIMIT_PATTERNS = [
    re.compile(r"429\s+Too\s+Many\s+Requests", re.I),
    re.compile(r"RateLimitError", re.I),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"quota\s+exceeded", re.I),
    re.compile(r"insufficient_quota", re.I),
]

API_AUTH_PATTERNS = [
    re.compile(r"401\s+Unauthorized", re.I),
    re.compile(r"AuthenticationError", re.I),
    re.compile(r"invalid.*api.*key", re.I),
]

DOCKER_DOWN_PATTERNS = [
    re.compile(r"Cannot connect to the Docker daemon", re.I),
    re.compile(r"docker daemon is not running", re.I),
    re.compile(r"docker.*daemon.*not.*running", re.I),
]

# These are "expected" failures — the pipeline already handles them gracefully
EXPECTED_FAILURE_PATTERNS = [
    re.compile(r"LimitsExceeded", re.I),
    re.compile(r"empty.*plan.*returned", re.I),
    re.compile(r"output too short", re.I),
    re.compile(r"ContextWindowExceededError", re.I),
]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(WATCHDOG_LOG, mode="a"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

@dataclass
class WatchdogState:
    batch_id: str
    total_instances: int
    completed: int = 0
    current_instance: str | None = None
    status: str = "running"          # running | api_cooldown | repairing | completed | fatal
    api_cooldown_until: str | None = None
    last_error: str | None = None
    last_error_time: str | None = None
    last_heartbeat: str = ""
    claude_repair_count: int = 0
    docker_retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> WatchdogState:
        # Tolerate missing fields from older state files
        defaults = {
            "batch_id": "unknown",
            "total_instances": 0,
            "completed": 0,
            "current_instance": None,
            "status": "running",
            "api_cooldown_until": None,
            "last_error": None,
            "last_error_time": None,
            "last_heartbeat": "",
            "claude_repair_count": 0,
            "docker_retry_count": 0,
        }
        defaults.update(d)
        return cls(**defaults)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> WatchdogState:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            return WatchdogState.from_dict(data)
        except Exception as exc:
            logging.warning("Failed to load state file: %s. Starting fresh.", exc)
    return _init_state()


def save_state(state: WatchdogState) -> None:
    state.last_heartbeat = _now_iso()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def _init_state() -> WatchdogState:
    """Infer initial state from config.yaml and sample file."""
    import yaml

    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        raise SystemExit("config.yaml not found")

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    batch_id = (cfg.get("system") or {}).get("batch_id", "")
    if not batch_id:
        raise SystemExit("system.batch_id is empty in config.yaml")

    dataset = (cfg.get("system") or {}).get("dataset", "SWE-bench/SWE-bench_Verified")
    dataset_short = dataset.split("/")[-1]

    sample_file = Path(f"output/{dataset_short}/{batch_id}/sampled_instances.json")
    total = 0
    if sample_file.exists():
        try:
            total = len(json.loads(sample_file.read_text(encoding="utf-8"))["instances"])
        except Exception:
            pass

    # Count already-completed instances
    completed = 0
    batch_dir = Path(f"output/{dataset_short}/{batch_id}")
    if batch_dir.exists():
        for inst_dir in batch_dir.iterdir():
            if inst_dir.is_dir() and (inst_dir / "result.json").exists():
                completed += 1

    state = WatchdogState(
        batch_id=batch_id,
        total_instances=total,
        completed=completed,
        status="running",
    )
    save_state(state)
    logging.info(
        "Initialized state: batch=%s total=%d completed=%d",
        batch_id, total, completed,
    )
    return state


# ---------------------------------------------------------------------------
# Tmux helpers
# ---------------------------------------------------------------------------

def _tmux_session_exists(name: str) -> bool:
    try:
        subprocess.run(
            ["tmux", "has-session", "-t", name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _kill_tmux_session(name: str) -> None:
    if _tmux_session_exists(name):
        logging.info("Killing tmux session: %s", name)
        subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)
        time.sleep(2)


def start_batch(state: WatchdogState) -> None:
    """Start (or restart) the batch runner in a tmux session."""
    _kill_tmux_session(BATCH_TMUX_SESSION)

    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& bash scripts/run_batch_verified.sh"
    )

    logging.info("Starting batch tmux session: %s", BATCH_TMUX_SESSION)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", BATCH_TMUX_SESSION, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(BATCH_TMUX_SESSION):
        logging.info("Batch session started successfully.")
    else:
        logging.error("Failed to start batch tmux session!")


# ---------------------------------------------------------------------------
# Log analysis
# ---------------------------------------------------------------------------

def _tail_file(path: Path, lines: int = 200) -> str:
    if not path.exists():
        return ""
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout
    except Exception:
        return ""


def _grep_patterns(text: str, patterns: list[re.Pattern]) -> list[str]:
    matches = []
    for line in text.splitlines():
        for pat in patterns:
            if pat.search(line):
                matches.append(line.strip())
                break
    return matches


def analyze_recent_logs() -> dict:
    """Return a dict describing the most recent batch activity."""
    master_tail = _tail_file(MASTER_LOG, 100)
    result = {
        "master_tail": master_tail,
        "api_rate_limited": bool(_grep_patterns(master_tail, API_RATE_LIMIT_PATTERNS)),
        "api_auth_failed": bool(_grep_patterns(master_tail, API_AUTH_PATTERNS)),
        "docker_down": bool(_grep_patterns(master_tail, DOCKER_DOWN_PATTERNS)),
        "expected_failure": bool(_grep_patterns(master_tail, EXPECTED_FAILURE_PATTERNS)),
    }

    # Try to identify current instance from most recent START line
    current = None
    for line in reversed(master_tail.splitlines()):
        m = re.search(r"START\s+(\S+)", line)
        if m:
            current = m.group(1)
            break
    result["current_instance"] = current

    # Count completed from master log
    completed = 0
    for line in master_tail.splitlines():
        if " DONE " in line or " SKIP " in line:
            completed += 1
    result["log_completed"] = completed

    return result


def is_batch_hung() -> bool:
    """True if the master log hasn't been modified in > HANG_TIMEOUT_SECONDS."""
    if not MASTER_LOG.exists():
        return False
    mtime = MASTER_LOG.stat().st_mtime
    age = time.time() - mtime
    if age > HANG_TIMEOUT_SECONDS:
        logging.warning("Master log stale for %.0f min (> %d min threshold)", age / 60, HANG_TIMEOUT_SECONDS / 60)
        return True
    return False


# ---------------------------------------------------------------------------
# Disk monitoring
# ---------------------------------------------------------------------------

def check_disk_space() -> bool:
    try:
        stat = os.statvfs(".")
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        if free_gb < DISK_MIN_FREE_GB:
            logging.error("Disk space critically low: %.1f GB free (min %d GB)", free_gb, DISK_MIN_FREE_GB)
            return False
        return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Claude Code repair
# ---------------------------------------------------------------------------

def invoke_claude_repair(state: WatchdogState, error_lines: list[str]) -> None:
    """Spawn a tmux session with Claude Code CLI to fix the bug."""
    _kill_tmux_session(REPAIR_TMUX_SESSION)

    error_text = "\n".join(error_lines[:20])
    log_snippet = _tail_file(MASTER_LOG, 100)

    prompt = f"""The PCT batch runner (plan-code-test pipeline) encountered an error that requires code changes.

## Error
```
{error_text}
```

## Recent master log context
```
{log_snippet}
```

## Your task
1. Read the relevant source files in `src/` to understand the error.
2. Fix the bug. Make minimal, focused changes.
3. Run the FULL test suite with `python -m pytest tests/ -q` to verify no regressions.
4. If your fix introduces NEW functionality or NEW branches that were not covered before,
   write additional tests in `tests/` to cover them. Then re-run the full test suite.
5. Only exit after ALL tests pass. If you cannot fix it after reasonable effort, exit anyway and note why.

Rules:
- Do NOT modify `config.yaml`, `.watchdog_state.json`, or anything in `output/`.
- Do NOT delete existing test files.
- Prefer editing existing files over creating new ones.
- The project uses Python 3.12 and pytest.
- If you add new tests, place them in the appropriate `tests/` subdirectory.
"""

    system_prompt = (
        "You are an autonomous repair agent for a Python software-engineering research pipeline. "
        "You fix code bugs, run the FULL regression test suite, write tests for any new code you add, "
        "and then exit. Be minimal and focused."
    )

    # Build the command that tmux will execute
    claude_cmd = (
        f"claude -p "
        f"--permission-mode bypassPermissions "
        f"--allowed-tools 'Bash,Edit,Read,Grep,Write' "
        f"--system-prompt {shlex.quote(system_prompt)} "
        f"{shlex.quote(prompt)}"
    )

    full_cmd = f"cd {shlex.quote(os.getcwd())} && {claude_cmd}"

    logging.info("Invoking Claude Code repair (tmux session: %s)", REPAIR_TMUX_SESSION)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", REPAIR_TMUX_SESSION, "bash", "-c", full_cmd],
        capture_output=True,
        check=False,
    )
    state.claude_repair_count += 1
    state.status = "repairing"
    save_state(state)


def is_repair_complete() -> bool:
    return not _tmux_session_exists(REPAIR_TMUX_SESSION)


def verify_repair() -> bool:
    """Run the FULL regression test suite after a repair.

    If tests fail, reverts code changes. If new tests were added by the
    repair agent, they are preserved as long as the suite passes.
    """
    logging.info("Running FULL regression test suite to verify repair...")
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logging.info("Repair verified: all tests pass.")
            # Log any new test files the repair agent may have added
            git_status = subprocess.run(
                ["git", "status", "--short", "tests/"],
                capture_output=True,
                text=True,
            )
            if git_status.stdout.strip():
                logging.info("New or modified test files detected:\n%s", git_status.stdout.strip())
            return True
        else:
            logging.warning(
                "Repair broke tests (rc=%d):\n%s\n%s",
                result.returncode,
                result.stdout[-800:] if len(result.stdout) > 800 else result.stdout,
                result.stderr[-400:] if len(result.stderr) > 400 else result.stderr,
            )
    except subprocess.TimeoutExpired:
        logging.warning("pytest timed out during repair verification (> 10 min).")

    # Revert source changes, but keep new test files for manual review
    logging.warning("Reverting src/ changes via git checkout...")
    subprocess.run(["git", "checkout", "--", "src/"], capture_output=True)
    subprocess.run(["git", "clean", "-fd", "src/"], capture_output=True)
    logging.warning("Test changes in tests/ are kept for manual review.")
    return False


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

def enter_api_cooldown(state: WatchdogState, error_msg: str) -> None:
    _kill_tmux_session(BATCH_TMUX_SESSION)
    cooldown_until = datetime.now(timezone.utc).timestamp() + API_COOLDOWN_SECONDS
    state.status = "api_cooldown"
    state.api_cooldown_until = datetime.fromtimestamp(cooldown_until, tz=timezone.utc).isoformat()
    state.last_error = error_msg
    state.last_error_time = _now_iso()
    save_state(state)
    logging.info(
        "Entering API cooldown until %s (%.1f hours from now)",
        state.api_cooldown_until,
        API_COOLDOWN_SECONDS / 3600,
    )


def cooldown_expired(state: WatchdogState) -> bool:
    if not state.api_cooldown_until:
        return True
    try:
        until = datetime.fromisoformat(state.api_cooldown_until)
        return datetime.now(timezone.utc) >= until
    except Exception:
        return True


def enter_fatal(state: WatchdogState, error_msg: str) -> None:
    _kill_tmux_session(BATCH_TMUX_SESSION)
    state.status = "fatal"
    state.last_error = error_msg
    state.last_error_time = _now_iso()
    save_state(state)
    logging.error("FATAL: %s", error_msg)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> int:
    _setup_logging()
    logging.info("=" * 60)
    logging.info("PCT Long-Run Watchdog starting")
    logging.info("=" * 60)

    # Validate environment
    if not os.environ.get("DEEPSEEK_API_KEY"):
        logging.error("DEEPSEEK_API_KEY not set")
        return 1

    state = load_state()

    # Main event loop
    while True:
        save_state(state)

        if state.status == "completed":
            logging.info("Batch already completed. Exiting watchdog.")
            return 0

        if state.status == "fatal":
            logging.error("Watchdog is in fatal state. Manual intervention required.")
            logging.error("Last error: %s", state.last_error)
            return 1

        if state.status == "api_cooldown":
            if cooldown_expired(state):
                logging.info("API cooldown expired. Resuming batch.")
                state.status = "running"
                state.api_cooldown_until = None
                state.docker_retry_count = 0
                save_state(state)
                start_batch(state)
            else:
                logging.info("API cooldown active. Next check in 60s.")
                time.sleep(60)
                continue

        if state.status == "repairing":
            if is_repair_complete():
                logging.info("Claude repair session ended.")
                verify_repair()
                state.status = "running"
                state.last_error = None
                save_state(state)
                start_batch(state)
            else:
                time.sleep(REPAIR_CHECK_INTERVAL)
                continue

        # --- status == "running" ---

        if not check_disk_space():
            logging.error("Disk space too low. Pausing.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        batch_alive = _tmux_session_exists(BATCH_TMUX_SESSION)

        if not batch_alive:
            logging.warning("Batch session not found. Analyzing logs...")
            log_analysis = analyze_recent_logs()

            if log_analysis["api_auth_failed"]:
                enter_fatal(state, "DeepSeek API authentication failed (401). Check DEEPSEEK_API_KEY.")
                continue

            if log_analysis["api_rate_limited"]:
                enter_api_cooldown(state, "DeepSeek API rate limited (429/quota).")
                continue

            if log_analysis["docker_down"]:
                state.docker_retry_count += 1
                if state.docker_retry_count > DOCKER_MAX_RETRIES:
                    enter_fatal(state, f"Docker daemon unreachable after {DOCKER_MAX_RETRIES} retries.")
                    continue
                logging.warning(
                    "Docker daemon not running (retry %d/%d). Waiting %d min...",
                    state.docker_retry_count, DOCKER_MAX_RETRIES, DOCKER_RETRY_INTERVAL // 60,
                )
                save_state(state)
                time.sleep(DOCKER_RETRY_INTERVAL)
                continue

            # Unknown failure — could be a code bug
            master_tail = log_analysis["master_tail"]
            # Collect last few error-looking lines
            error_lines = [ln for ln in master_tail.splitlines() if "ERROR" in ln or "error" in ln.lower()][-10:]
            if not error_lines:
                error_lines = ["Batch exited with unknown cause."]

            logging.error("Unknown batch failure. Invoking Claude repair.")
            invoke_claude_repair(state, error_lines)
            continue

        # Batch is alive — check for hang
        if is_batch_hung():
            logging.warning("Batch appears hung. Restarting...")
            _kill_tmux_session(BATCH_TMUX_SESSION)
            time.sleep(5)
            start_batch(state)
            continue

        # Update progress
        log_analysis = analyze_recent_logs()
        if log_analysis["current_instance"]:
            state.current_instance = log_analysis["current_instance"]
        # Use max of disk count and log count for completed
        disk_completed = state.completed
        log_completed = log_analysis.get("log_completed", 0)
        state.completed = max(disk_completed, log_completed)

        if state.total_instances > 0 and state.completed >= state.total_instances:
            logging.info("All %d instances appear complete.", state.total_instances)
            state.status = "completed"
            save_state(state)
            continue

        logging.info(
            "Healthy: completed=%d/%d current=%s",
            state.completed,
            state.total_instances,
            state.current_instance or "—",
        )
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.info("Watchdog stopped by user.")
        sys.exit(0)
