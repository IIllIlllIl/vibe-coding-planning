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
HANG_TIMEOUT_SECONDS = 8 * 3600       # 8 hours with no log change = hung
API_COOLDOWN_SECONDS = 5 * 3600       # 5 hours for DeepSeek rate-limit recovery
REPAIR_BACKOFF_SECONDS = 10 * 3600    # 10 hours after max repair attempts exceeded
DOCKER_RETRY_INTERVAL = 300           # base 5 min; exponentiated on repeated failures
DOCKER_MAX_RETRIES = 12               # 12 retries with backoff, then fatal
REPAIR_CHECK_INTERVAL = 60            # 1 min while waiting for Claude repair
DISK_MIN_FREE_GB = 10                 # Pause if less than 10 GB free
MAX_REPAIR_ATTEMPTS = 3               # Claude repair limit before long cooldown
DOCKER_PRUNE_INTERVAL_CHECKS = 6      # Run `docker system prune -f` every N checks
# When the batch tmux session is gone, poll the master log for the
# `=== Batch end ===` marker before treating the disappearance as a crash.
# A single 5 s sleep proved too tight on a busy filesystem: tee can lag the
# tmux exit by tens of seconds, causing the watchdog to mis-diagnose a clean
# finish as a crash and burn a Claude repair attempt.
LOG_SETTLE_POLL_SECONDS = 5
LOG_SETTLE_MAX_SECONDS = 30

BATCH_TMUX_SESSION = "pct-batch"
ANALYSIS_TMUX_SESSION = "pct-analysis"
AGGREGATION_TMUX_SESSION = "pct-aggregation"
REVIEW_TMUX_SESSION = "pct-review"
REPAIR_TMUX_SESSION = "pct-repair"
CHECKER_TMUX_SESSION = "pct-checker"
MASTER_LOG = Path("logs/batch_run.log")
ANALYSIS_LOG = Path("logs/analysis_run.log")
AGGREGATION_LOG = Path("logs/aggregation_run.log")
REVIEW_LOG = Path("logs/review_run.log")
CHECKER_LOG = Path("logs/checker_run.log")
WATCHDOG_LOG = Path("logs/watchdog.log")
STATE_FILE = Path("output/.watchdog_state.json")

# Transient API errors -> cooldown (rate limits, network blips, 5xx)
API_RATE_LIMIT_PATTERNS = [
    re.compile(r"429\s+Too\s+Many\s+Requests", re.I),
    re.compile(r"RateLimitError", re.I),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"quota\s+exceeded", re.I),
    re.compile(r"insufficient_quota", re.I),
    # Network / server-side transients that look like API issues
    re.compile(r"ConnectionError", re.I),
    re.compile(r"ConnectTimeout", re.I),
    re.compile(r"ReadTimeout", re.I),
    re.compile(r"503\s+Service\s+Unavailable", re.I),
    re.compile(r"502\s+Bad\s+Gateway", re.I),
    re.compile(r"504\s+Gateway\s+Timeout", re.I),
    re.compile(r"500\s+Internal\s+Server\s+Error", re.I),
    re.compile(r"ServiceUnavailableError", re.I),
    re.compile(r"InternalServerError", re.I),
    re.compile(r"connection\s+reset\s+by\s+peer", re.I),
    re.compile(r"temporary\s+failure\s+in\s+name\s+resolution", re.I),
    re.compile(r"name\s+or\s+service\s+not\s+known", re.I),
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
    # Analysis phase tracking (flash → pro, serial)
    analysis_phase: str | None = None   # None | "flash" | "pro" | "done"
    analysis_completed: int = 0
    # Aggregation phase tracking (Input-Aware Tree Merge)
    aggregation_phase: str | None = None  # None | "flash" | "pro" | "done"
    # Review phase tracking (quality gate after pro analysis)
    review_phase: str | None = None     # None | "pending" | "reviewing" | "reworking" | "done"
    rework_queue: list[str] = None      # instance_ids that need rework
    rework_attempts: dict[str, int] = None  # per-instance retry counter
    review_results: dict[str, dict] = None  # per-instance quality scores
    # Checker evaluation phase tracking (Plan-Check-Code on Pro Python)
    checker_phase: str | None = None    # None | "running" | "done"

    def __post_init__(self):
        if self.rework_queue is None:
            self.rework_queue = []
        if self.rework_attempts is None:
            self.rework_attempts = {}
        if self.review_results is None:
            self.review_results = {}

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
            "analysis_phase": None,
            "analysis_completed": 0,
            "aggregation_phase": None,
            "review_phase": None,
            "rework_queue": [],
            "rework_attempts": {},
            "review_results": {},
            "checker_phase": None,
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

    # Try batch-scoped sample file first (Verified format)
    sample_file = Path(f"output/{dataset_short}/{batch_id}/sampled_instances.json")
    total = 0
    if sample_file.exists():
        try:
            total = len(json.loads(sample_file.read_text(encoding="utf-8"))["instances"])
        except Exception:
            pass

    # Fall back to Pro instance list files
    if total == 0:
        for pro_file in ["pro_ansible_instances.json", "pro_python_instances.json"]:
            pf = Path(pro_file)
            if pf.exists():
                try:
                    data = json.loads(pf.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        total = len(data)
                    elif isinstance(data, dict):
                        total = len(data.get("instances", []))
                    if total > 0:
                        break
                except Exception:
                    pass

    # Count already-completed instances (robust to corrupt JSON)
    completed = 0
    batch_dir = Path(f"output/{dataset_short}/{batch_id}")
    if batch_dir.exists():
        for inst_dir in batch_dir.iterdir():
            if not inst_dir.is_dir():
                continue
            result_file = inst_dir / "result.json"
            if result_file.exists():
                try:
                    json.loads(result_file.read_text(encoding="utf-8"))
                    completed += 1
                except json.JSONDecodeError:
                    logging.warning(
                        "Corrupt result.json at %s — not counting as complete.",
                        result_file,
                    )
                except Exception:
                    pass

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
        f"&& bash scripts/run_batch.sh"
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


def _resolve_analysis_model(phase: str | None) -> str:
    """Return the model name for the given analysis phase.

    Legacy phases "flash" and "pro" map to hard-coded DeepSeek models.
    For any other phase (e.g. "kimi"), the model is read from
    config.yaml ``analysis.model`` so that users can switch providers
    without editing the watchdog.
    """
    legacy = {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"}
    if phase in legacy:
        return legacy[phase]
    # Non-legacy phase: read from config.yaml
    try:
        import yaml
        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        return (cfg.get("analysis") or {}).get("model", "deepseek-v4-flash")
    except Exception:
        return "deepseek-v4-flash"


def start_analysis(state: WatchdogState) -> None:
    """Start (or restart) the analysis runner in a tmux session.

    The model is determined by state.analysis_phase ("flash", "pro", or a
    custom phase like "kimi").
    """
    _kill_tmux_session(ANALYSIS_TMUX_SESSION)

    model_name = _resolve_analysis_model(state.analysis_phase)
    output_dir = f"./output/analysis_{state.analysis_phase or 'unknown'}"

    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& bash scripts/run_analysis.sh --model {model_name} --output-dir {output_dir}"
    )

    logging.info(
        "Starting analysis tmux session: %s (phase=%s model=%s)",
        ANALYSIS_TMUX_SESSION,
        state.analysis_phase,
        model_name,
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", ANALYSIS_TMUX_SESSION, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(ANALYSIS_TMUX_SESSION):
        logging.info("Analysis session started successfully.")
    else:
        logging.error("Failed to start analysis tmux session!")


def start_aggregation(state: WatchdogState) -> None:
    """Start (or restart) the rule-aggregation runner in a tmux session.

    The model matches the aggregation_phase ("flash", "pro", or a custom
    phase like "kimi").
    Reads per_case/*.json from the corresponding analysis output directory
    and writes aggregated_rules.json back to the same directory.
    """
    _kill_tmux_session(AGGREGATION_TMUX_SESSION)

    model_name = _resolve_analysis_model(state.aggregation_phase)

    input_dir = f"./output/analysis_{state.aggregation_phase or 'unknown'}/per_case"
    output_dir = f"./output/analysis_{state.aggregation_phase or 'unknown'}"

    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& python -m src.analysis "
        f"--input {shlex.quote(input_dir)} "
        f"--output {shlex.quote(output_dir)} "
        f"--model {shlex.quote(model_name)} "
        f"--aggregate "
        f">> logs/aggregation_run.log 2>&1"
    )

    AGGREGATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    AGGREGATION_LOG.touch(exist_ok=True)

    logging.info(
        "Starting aggregation tmux session: %s (phase=%s model=%s)",
        AGGREGATION_TMUX_SESSION,
        state.aggregation_phase,
        model_name,
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", AGGREGATION_TMUX_SESSION, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(AGGREGATION_TMUX_SESSION):
        logging.info("Aggregation session started successfully.")
    else:
        logging.error("Failed to start aggregation tmux session!")


def start_checker_eval(state: WatchdogState) -> None:
    """Start (or restart) the checker evaluation runner in a tmux session.

    Runs Plan-Check-Code on SWE-bench Pro Python instances using
    scripts/evaluate_checker.py.
    """
    _kill_tmux_session(CHECKER_TMUX_SESSION)

    # Ensure instance list file exists
    instances_file = Path("pro_ansible_instances.json")
    if not instances_file.exists():
        instances_file = Path("pro_python_instances.json")
    if not instances_file.exists():
        logging.warning("Pro instance list not found. Generating Python list...")
        instances_file = Path("output/pro_python_instances.json")
        _generate_pro_python_instances(instances_file)

    output_dir = "./output/checker_eval/pro_python"
    dataset = "ScaleAI/SWE-bench_Pro"

    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& python scripts/evaluate_checker.py "
        f"--config config.yaml "
        f"--dataset {shlex.quote(dataset)} "
        f"--instances {shlex.quote(str(instances_file))} "
        f"--output {shlex.quote(output_dir)} "
        f"2>&1 | tee -a {shlex.quote(str(CHECKER_LOG))}"
    )

    CHECKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    CHECKER_LOG.touch(exist_ok=True)

    logging.info(
        "Starting checker eval tmux session: %s (dataset=%s instances=%s)",
        CHECKER_TMUX_SESSION,
        dataset,
        instances_file,
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", CHECKER_TMUX_SESSION, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(CHECKER_TMUX_SESSION):
        logging.info("Checker eval session started successfully.")
    else:
        logging.error("Failed to start checker eval tmux session!")


def _generate_pro_python_instances(output_path: Path) -> None:
    """Generate a JSON file with SWE-bench Pro Python instance IDs."""
    try:
        from datasets import load_dataset
        ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
        python_instances = [
            x["instance_id"] for x in ds if x.get("repo_language") == "python"
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(python_instances, indent=2), encoding="utf-8"
        )
        logging.info(
            "Generated %s with %d Python instances", output_path, len(python_instances)
        )
    except Exception as exc:
        logging.error("Failed to generate Pro Python instance list: %s", exc)
        # Create empty file so watchdog doesn't keep retrying
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("[]", encoding="utf-8")


def start_review(state: WatchdogState, instance_ids: list[str] | None = None) -> None:
    """Start (or restart) the LLM review runner in a tmux session.

    The review target is inferred from ``state.analysis_phase`` (the phase
    whose rules are being reviewed: "flash" or "pro").
    """
    _kill_tmux_session(REVIEW_TMUX_SESSION)

    review_target = state.analysis_phase or "pro"
    model_name = _resolve_analysis_model(review_target)

    output_dir = f"./output/analysis_{review_target}"
    data_dir = "./output/SWE-bench_Verified/reflect_success_cases"

    instances_arg = ""
    if instance_ids:
        instances_arg = " ".join(
            f"--instance {shlex.quote(iid)}" for iid in instance_ids
        )

    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& python -m src.analysis.review_cli "
        f"--data-dir {shlex.quote(data_dir)} "
        f"--output-dir {shlex.quote(output_dir)} "
        f"--model {shlex.quote(model_name)} "
        f"{instances_arg} "
        f">> logs/review_run.log 2>&1"
    )

    # Ensure the review log file exists so is_log_stale works immediately
    REVIEW_LOG.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_LOG.touch(exist_ok=True)

    logging.info(
        "Starting review tmux session: %s (target=%s model=%s instances=%s)",
        REVIEW_TMUX_SESSION,
        review_target,
        model_name,
        len(instance_ids) if instance_ids else "all",
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", REVIEW_TMUX_SESSION, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(REVIEW_TMUX_SESSION):
        logging.info("Review session started successfully.")
    else:
        logging.error("Failed to start review tmux session!")


# ---------------------------------------------------------------------------
# Rule quality review
# ---------------------------------------------------------------------------

# Patterns that indicate the rule contains implementation details rather than
# generalizable reasoning strategies.
IMPLEMENTATION_DETAIL_PATTERNS = [
    re.compile(r"\/\w+\/\w+\.\w+"),           # file paths like /testbed/foo.py
    re.compile(r"\bline\s*\d+\b", re.I),      # line numbers
    re.compile(r"\bln\.?\s*\d+\b", re.I),     # ln 42
    re.compile(r"\b\d+\s*:\s*\d+\b"),         # 42:13 (line:col)
]

# Patterns that indicate format pollution from the agent
FORMAT_POLLUTION_PATTERNS = [
    re.compile(r"^#+\s*"),                      # markdown headers
    re.compile(r"<\｜\｜DSML\｜\｜"),            # DSML tool tags
    re.compile(r"<tool_calls>"),                 # XML tool calls
    re.compile(r"<tool_call\s+"),                # XML tool call open
    re.compile(r"<invoke\s+"),                   # XML invoke
    re.compile(r"<parameter\s+"),                # XML parameter
]

# Minimum quality score (0-100) for a rule to pass review
RULE_QUALITY_PASS_THRESHOLD = 70
RULE_REWORK_MAX_ATTEMPTS = 3


def evaluate_rule_quality(rule_text: str) -> dict:
    """Evaluate the quality of an extracted contrastive rule.

    Returns a dict with:
        - score (int): 0-100 composite score
        - passed (bool): True if score >= RULE_QUALITY_PASS_THRESHOLD
        - checks (dict): individual check results with boolean values
    """
    checks: dict[str, bool] = {}
    score = 0
    max_score = 100

    # 1. Non-empty (10 pts)
    checks["non_empty"] = bool(rule_text and rule_text.strip())
    if checks["non_empty"]:
        score += 10

    # 2. Length reasonable (10 pts)
    length = len(rule_text) if rule_text else 0
    checks["length_ok"] = 50 <= length <= 5000
    if checks["length_ok"]:
        score += 10

    # 3. Starts with "When " (15 pts)
    checks["starts_with_when"] = rule_text.strip().lower().startswith("when ")
    if checks["starts_with_when"]:
        score += 15

    # 4. Contains " because " (15 pts)
    checks["has_because"] = " because " in rule_text.lower()
    if checks["has_because"]:
        score += 15

    # 5. No markdown/format pollution (15 pts)
    polluted = any(p.search(rule_text) for p in FORMAT_POLLUTION_PATTERNS)
    checks["no_format_pollution"] = not polluted
    if checks["no_format_pollution"]:
        score += 15

    # 6. No implementation details (15 pts)
    has_impl = any(p.search(rule_text) for p in IMPLEMENTATION_DETAIL_PATTERNS)
    checks["no_impl_details"] = not has_impl
    if checks["no_impl_details"]:
        score += 15

    # 7. Has substantive strategy (20 pts)
    # Look for strategy indicators: cognitive verbs, reasoning patterns
    strategy_indicators = [
        "should", "must", "need to", "strategy", "approach",
        "instead", "rather than", "focus on", "prioritize",
        "verify", "check", "ensure", "validate", "confirm",
        "trace", "inspect", "examine", "analyze", "compare",
        "recognize", "identify", "distinguish", "differentiate",
    ]
    has_strategy = any(ind in rule_text.lower() for ind in strategy_indicators)
    checks["has_strategy"] = has_strategy
    if has_strategy:
        score += 20

    return {
        "score": score,
        "passed": score >= RULE_QUALITY_PASS_THRESHOLD,
        "checks": checks,
        "max_score": max_score,
    }


def review_all_rules(output_dir: str, instance_ids: list[str] | None = None) -> tuple[list[str], dict[str, dict]]:
    """Review extracted rules in the given output directory.

    Args:
        output_dir: Path to analysis output dir (e.g. "./output/analysis_pro")
        instance_ids: If provided, only review these specific instances.
                      If None, review all instances found in per_case/*.json.

    Returns:
        (rework_queue, review_results) where:
            - rework_queue: list of instance_ids that failed review
            - review_results: dict mapping instance_id -> quality result dict
    """
    per_case_dir = Path(output_dir) / "per_case"
    if not per_case_dir.exists():
        logging.warning("No per_case dir found at %s", per_case_dir)
        return [], {}

    # Determine which files to examine
    if instance_ids is not None:
        files_to_check = []
        for iid in instance_ids:
            f = per_case_dir / f"{iid}.json"
            if f.exists():
                files_to_check.append(f)
            else:
                logging.warning("Result file missing for %s, treating as failed", iid)
    else:
        files_to_check = sorted(per_case_dir.glob("*.json"))

    rework_queue: list[str] = []
    review_results: dict[str, dict] = {}

    for result_file in files_to_check:
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except Exception:
            logging.warning("Cannot read result file: %s", result_file)
            continue

        instance_id = data.get("instance_id", result_file.stem)
        rule_text = data.get("rule", "")
        quality = evaluate_rule_quality(rule_text)
        review_results[instance_id] = quality

        if not quality["passed"]:
            rework_queue.append(instance_id)
            logging.info(
                "Rule review FAILED for %s (score=%d/%d): %s",
                instance_id,
                quality["score"],
                quality["max_score"],
                {k: v for k, v in quality["checks"].items() if not v},
            )
        else:
            logging.info(
                "Rule review PASSED for %s (score=%d/%d)",
                instance_id,
                quality["score"],
                quality["max_score"],
            )

    return rework_queue, review_results


def start_rework(instance_id: str, model_name: str, output_dir: str) -> None:
    """Start a single-case rework analysis in a dedicated tmux session.

    The session name includes the instance_id so the watchdog can track
    individual rework jobs.
    """
    session_name = f"pct-rework-{instance_id.replace('__', '-')}"
    _kill_tmux_session(session_name)

    # Delete the old result so the analysis CLI will re-run this case
    old_result = Path(output_dir) / "per_case" / f"{instance_id}.json"
    if old_result.exists():
        old_result.unlink()
        logging.info("Deleted old result for rework: %s", old_result)

    input_dir = "./output/SWE-bench_Verified/reflect_success_cases"
    cmd = (
        f"cd {shlex.quote(os.getcwd())} "
        f"&& source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh "
        f"&& conda activate mini-swe "
        f"&& python -m src.analysis "
        f"--input {shlex.quote(input_dir)} "
        f"--output {shlex.quote(output_dir)} "
        f"--model {shlex.quote(model_name)} "
        f"--instance {shlex.quote(instance_id)}"
    )

    logging.info(
        "Starting rework session: %s (instance=%s model=%s)",
        session_name, instance_id, model_name,
    )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "bash", "-c", cmd],
        capture_output=True,
        check=False,
    )
    time.sleep(3)

    if _tmux_session_exists(session_name):
        logging.info("Rework session started successfully: %s", session_name)
    else:
        logging.error("Failed to start rework session: %s", session_name)


def is_rework_complete(instance_id: str) -> bool:
    """Check if a rework tmux session has finished."""
    session_name = f"pct-rework-{instance_id.replace('__', '-')}"
    return not _tmux_session_exists(session_name)


def get_rework_log_path(instance_id: str) -> Path:
    """Return the log path for a rework session."""
    return Path(f"logs/rework_{instance_id.replace('__', '_')}.log")


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


def analyze_recent_logs(log_path: Path | None = None) -> dict:
    """Return a dict describing the most recent batch, analysis, or review activity.

    Args:
        log_path: Path to the master log to analyze. Defaults to MASTER_LOG
                  (the batch log). Pass ANALYSIS_LOG to analyze the analysis
                  runner, or REVIEW_LOG to analyze the review runner.
    """
    log_path = log_path or MASTER_LOG
    master_tail = _tail_file(log_path, 100)
    if log_path.name == REVIEW_LOG.name:
        end_marker = "=== Review end ==="
    elif log_path.name == MASTER_LOG.name:
        end_marker = "=== Batch end ==="
    elif log_path.name == CHECKER_LOG.name:
        end_marker = "=== Checker eval end ==="
    else:
        end_marker = "=== Analysis end ==="
    result = {
        "master_tail": master_tail,
        "api_rate_limited": bool(_grep_patterns(master_tail, API_RATE_LIMIT_PATTERNS)),
        "api_auth_failed": bool(_grep_patterns(master_tail, API_AUTH_PATTERNS)),
        "docker_down": bool(_grep_patterns(master_tail, DOCKER_DOWN_PATTERNS)),
        "expected_failure": bool(_grep_patterns(master_tail, EXPECTED_FAILURE_PATTERNS)),
        "batch_completed": end_marker in master_tail,
    }

    # Try to identify current instance from most recent START line
    current = None
    for line in reversed(master_tail.splitlines()):
        m = re.search(r"START\s+(\S+)", line)
        if m:
            current = m.group(1)
            break
    result["current_instance"] = current

    # Count completed (in the batch-progress sense) from the master log.
    # "Completed" here matches the project definition: the agent either
    # produced a passing patch OR exhausted its retry budget — both paths
    # leave src.main writing a result.json and the master log recording
    # DONE. SKIP means the instance already had a result.json from a prior
    # run, which also counts. FAIL means src.main itself crashed before
    # writing result.json (e.g. the current Jinja2 second-pass bug), so the
    # instance is NOT done and must be retried after a code fix — counting
    # FAIL here would let persistent crashers falsely satisfy the
    # "all instances complete" check in main().
    completed = 0
    for line in master_tail.splitlines():
        if " DONE " in line or " SKIP " in line:
            completed += 1
    result["log_completed"] = completed

    return result


def is_log_stale(log_path: Path | None = None) -> bool:
    """True if the given log hasn't been modified in > HANG_TIMEOUT_SECONDS.

    Args:
        log_path: Defaults to MASTER_LOG. Pass ANALYSIS_LOG to check
                  the analysis runner.
    """
    log_path = log_path or MASTER_LOG
    if not log_path.exists():
        return False
    mtime = log_path.stat().st_mtime
    age = time.time() - mtime
    if age > HANG_TIMEOUT_SECONDS:
        logging.warning(
            "%s stale for %.0f min (> %d min threshold)",
            log_path.name, age / 60, HANG_TIMEOUT_SECONDS / 60,
        )
        return True
    return False


def is_batch_hung() -> bool:
    """Backward-compatible wrapper: checks the batch master log."""
    return is_log_stale(MASTER_LOG)


# ---------------------------------------------------------------------------
# Disk / Docker storage monitoring
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


def cleanup_docker() -> None:
    """Prune unused Docker objects to free space and prevent container leaks.

    Runs non-interactively; failures are logged but never fatal.
    """
    logging.info("Running docker system prune -f ...")
    try:
        result = subprocess.run(
            ["docker", "system", "prune", "-f"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            out = result.stdout.strip()
            if out:
                logging.info("Docker prune output: %s", out)
            else:
                logging.info("Docker prune completed (nothing removed).")
        else:
            err = result.stderr.strip()[:400]
            logging.warning("Docker prune failed (rc=%d): %s", result.returncode, err)
    except FileNotFoundError:
        logging.warning("Docker CLI not found; skipping prune.")
    except subprocess.TimeoutExpired:
        logging.warning("Docker prune timed out (> 120 s).")
    except Exception as exc:
        logging.warning("Docker prune unexpected error: %s", exc)


def _load_enable_review() -> bool:
    """Read ``analysis.enable_review`` from config.yaml.

    Returns ``False`` if the file is missing, unreadable, or the key
    is absent, so that review is opt-in.
    """
    try:
        import yaml

        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        analysis = cfg.get("analysis", {})
        val = analysis.get("enable_review", False)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("true", "1", "yes", "on")
        return bool(val)
    except Exception:
        return False


def _should_start_analysis_after_batch() -> bool:
    """Only run post-batch rule analysis for Verified exploration batches."""
    try:
        import yaml

        cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
        dataset = (cfg.get("system") or {}).get("dataset", "")
        return "SWE-bench_Verified" in dataset or "Verified" in dataset
    except Exception:
        return False


def docker_backoff_wait(retry_count: int) -> int:
    """Exponential backoff for Docker daemon retries.

    Base 5 min, doubling each retry, capped at 60 min.
    retry_count=1 -> 5 min, retry_count=2 -> 10 min, etc.
    """
    wait = DOCKER_RETRY_INTERVAL * (2 ** min(retry_count - 1, 4))
    return min(wait, 3600)


# ---------------------------------------------------------------------------
# Claude Code repair
# ---------------------------------------------------------------------------

def invoke_claude_repair(state: WatchdogState, error_lines: list[str]) -> bool:
    """Spawn a tmux session with Claude Code CLI to fix the bug.

    Returns True if the repair tmux session was actually launched, or False
    if we aborted at the last moment (e.g. the master log just caught up
    with a buffered ``=== Batch end ===`` line and the batch is in fact
    complete). Caller can use the return value to skip post-repair
    bookkeeping when no repair was launched.
    """
    log_snippet = _tail_file(MASTER_LOG, 100)

    # Defensive recheck: between analyze_recent_logs() in the main loop and
    # this point, the master log may have caught up with the buffered
    # `=== Batch end ===` line that the batch script tees at exit. The
    # earlier "session gone + error lines present" diagnosis was a race
    # against tee flushing, not a real crash. Marking the batch complete
    # here saves a Claude repair attempt (and the MAX_REPAIR_ATTEMPTS
    # budget) for an actual bug. We deliberately do NOT overwrite
    # state.completed — that field tracks real progress (result.json files
    # on disk), and forging it to total_instances would mask instances
    # that crashed before writing result.json.
    if "=== Batch end ===" in log_snippet:
        logging.info(
            "End marker observed just before invoking repair; the earlier "
            "diagnosis was a race against the master log's tee flush. "
            "Marking batch complete instead of invoking Claude."
        )
        state.status = "completed"
        save_state(state)
        return False

    _kill_tmux_session(REPAIR_TMUX_SESSION)

    error_text = "\n".join(error_lines[:20])

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
- Do NOT modify `scripts/long_run_watchdog.py` or any other file in `scripts/`.
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
    return True


def is_repair_complete() -> bool:
    return not _tmux_session_exists(REPAIR_TMUX_SESSION)


def verify_repair() -> bool:
    """Run the FULL regression test suite after a repair.

    If tests fail, revert ``src/``, ``tests/``, and
    ``scripts/long_run_watchdog.py`` to HEAD. Earlier versions of this
    function kept tests/ "for manual review" on failure, but real-world
    repair runs have shown the agent may hallucinate a fix and rewrite
    existing test assertions to match the hallucinated behaviour — leaving
    those tests on disk poisons the next regression run. Reverting tests/
    too means we lose any *legitimate* new tests the agent wrote, which is
    an acceptable price for a recoverable test suite during a multi-day
    unattended run.

    ``scripts/long_run_watchdog.py`` is also reverted because the repair
    agent runs inside a tmux session and could accidentally modify the
    very file that is supervising the batch. The prompt already forbids
    touching ``scripts/``, but the revert is a belt-and-suspenders safety
    net.
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

    # Revert src/, tests/, AND scripts/long_run_watchdog.py — see docstring
    # for why tests/ is no longer preserved on failure. `git checkout --`
    # reverts tracked-file edits; `git clean -fd` removes untracked files
    # the repair agent may have created. We also revert the watchdog script
    # because the repair agent runs inside tmux and could accidentally (or
    # maliciously) corrupt the very process that is supervising the batch.
    logging.warning(
        "Reverting src/, tests/, and scripts/long_run_watchdog.py changes "
        "via git checkout + clean..."
    )
    subprocess.run(
        ["git", "checkout", "--", "src/", "tests/", "scripts/long_run_watchdog.py"],
        capture_output=True,
    )
    subprocess.run(
        ["git", "clean", "-fd", "src/", "tests/", "scripts/long_run_watchdog.py"],
        capture_output=True,
    )
    return False


# ---------------------------------------------------------------------------
# Recovery actions
# ---------------------------------------------------------------------------

def enter_api_cooldown(state: WatchdogState, error_msg: str, duration_seconds: int | None = None) -> None:
    _kill_tmux_session(BATCH_TMUX_SESSION)
    duration = duration_seconds if duration_seconds is not None else API_COOLDOWN_SECONDS
    cooldown_until = datetime.now(timezone.utc).timestamp() + duration
    state.status = "api_cooldown"
    state.api_cooldown_until = datetime.fromtimestamp(cooldown_until, tz=timezone.utc).isoformat()
    state.last_error = error_msg
    state.last_error_time = _now_iso()
    save_state(state)
    logging.info(
        "Entering API cooldown until %s (%.1f hours from now)",
        state.api_cooldown_until,
        duration / 3600,
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
    enable_review = _load_enable_review()
    logging.info("Watchdog config: enable_review=%s", enable_review)

    # Determine active mode: checker > aggregation > review > analysis > batch
    in_checker = state.checker_phase == "running"
    in_aggregation = state.aggregation_phase in ("flash", "pro") and not in_checker
    in_review = state.review_phase in ("reviewing", "reworking") and not in_aggregation and not in_checker
    in_analysis = state.analysis_phase in ("flash", "pro") and not in_aggregation and not in_review and not in_checker
    if in_checker:
        active_session = CHECKER_TMUX_SESSION
        active_log = CHECKER_LOG
        start_fn = start_checker_eval
    elif in_aggregation:
        active_session = AGGREGATION_TMUX_SESSION
        active_log = AGGREGATION_LOG
        start_fn = start_aggregation
    elif in_review:
        active_session = REVIEW_TMUX_SESSION
        active_log = REVIEW_LOG
        start_fn = lambda s: start_review(s, instance_ids=s.rework_queue or None)
    elif in_analysis:
        active_session = ANALYSIS_TMUX_SESSION
        active_log = ANALYSIS_LOG
        start_fn = start_analysis
    else:
        active_session = BATCH_TMUX_SESSION
        active_log = MASTER_LOG
        start_fn = start_batch

    # Cold-start: if status is "running" but no active tmux session exists
    # (fresh launch, or resume after Mac reboot / manual watchdog restart),
    # kick off the appropriate runner before entering the watch loop.
    if state.status == "running" and not _tmux_session_exists(active_session):
        if in_checker:
            logging.info("Cold start: launching initial checker eval session.")
        elif in_aggregation:
            logging.info("Cold start: launching initial aggregation session (phase=%s).", state.aggregation_phase)
        elif in_review:
            logging.info("Cold start: launching initial review session (target=%s).", state.analysis_phase)
        elif in_analysis:
            logging.info("Cold start: launching initial analysis session (phase=%s).", state.analysis_phase)
        else:
            logging.info("Cold start: launching initial batch session.")
        start_fn(state)
        time.sleep(5)

    # Main event loop
    while True:
        save_state(state)

        if state.status == "fatal":
            logging.error("Watchdog is in fatal state. Manual intervention required.")
            logging.error("Last error: %s", state.last_error)
            return 1

        if state.status == "api_cooldown":
            if cooldown_expired(state):
                mode_name = (
                    "checker" if in_checker else
                    ("aggregation" if in_aggregation else
                    ("review" if in_review else ("analysis" if in_analysis else "batch")))
                )
                logging.info("API cooldown expired. Resuming %s.", mode_name)
                state.status = "running"
                state.api_cooldown_until = None
                state.docker_retry_count = 0
                state.claude_repair_count = 0
                save_state(state)
                start_fn(state)
            else:
                logging.info("API cooldown active. Next check in 60s.")
                time.sleep(60)
                continue

        if state.status == "repairing":
            if is_repair_complete():
                logging.info("Claude repair session ended.")
                if verify_repair():
                    state.claude_repair_count = 0
                state.status = "running"
                state.last_error = None
                save_state(state)
                start_fn(state)
            else:
                time.sleep(REPAIR_CHECK_INTERVAL)
                continue

        # --- status == "running" ---

        if not check_disk_space():
            logging.error("Disk space too low. Pausing.")
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # -----------------------------------------------------------------------
        # Aggregation phase (Input-Aware Tree Merge)
        # -----------------------------------------------------------------------
        if state.aggregation_phase in ("flash", "pro"):
            in_aggregation = True
            active_session = AGGREGATION_TMUX_SESSION
            active_log = AGGREGATION_LOG
            start_fn = start_aggregation

            if _tmux_session_exists(AGGREGATION_TMUX_SESSION):
                if is_log_stale(AGGREGATION_LOG):
                    logging.warning("Aggregation session appears hung. Restarting...")
                    _kill_tmux_session(AGGREGATION_TMUX_SESSION)
                    time.sleep(5)
                    start_fn(state)
                    continue
                logging.info("Aggregation session in progress. Next check in 60s.")
                time.sleep(60)
                continue

            # Session not running — check for output file (aggregation is a single-shot
            # python invocation without a shell-script end marker).
            output_dir = f"./output/analysis_{state.aggregation_phase}"
            agg_result_path = Path(output_dir) / "aggregated_rules.json"
            if agg_result_path.exists():
                logging.info("Aggregation complete. Output found at %s", agg_result_path)
                if state.aggregation_phase == "flash":
                    logging.info("Flash aggregation complete. Handing off to pro analysis.")
                    state.analysis_phase = "pro"
                    state.analysis_completed = 0
                    state.aggregation_phase = None
                    save_state(state)
                    start_analysis(state)
                    continue
                elif state.aggregation_phase == "pro":
                    logging.info("Pro aggregation complete. All work finished.")
                    return 0
            else:
                logging.warning(
                    "Aggregation session not running and no %s found. Restarting...",
                    agg_result_path,
                )
                start_fn(state)
                continue

        # -----------------------------------------------------------------------
        # Review phase (LLM-based quality gate)
        # -----------------------------------------------------------------------
        if state.review_phase == "reviewing":
            # Re-evaluate mode (review may have been set while we were in analysis)
            in_review = True
            active_session = REVIEW_TMUX_SESSION
            active_log = REVIEW_LOG
            start_fn = lambda s: start_review(s, instance_ids=s.rework_queue or None)

            # Check if review session is running
            if _tmux_session_exists(REVIEW_TMUX_SESSION):
                # Review is in progress — check for hang
                if is_log_stale(REVIEW_LOG):
                    logging.warning("Review session appears hung. Restarting...")
                    _kill_tmux_session(REVIEW_TMUX_SESSION)
                    time.sleep(5)
                    start_fn(state)
                    continue
                # Healthy — just wait
                logging.info("Review session in progress. Next check in 60s.")
                time.sleep(60)
                continue

            # Review session not running — check if it finished cleanly
            review_log_analysis = analyze_recent_logs(REVIEW_LOG)
            if review_log_analysis["batch_completed"]:
                logging.info("Review batch completed. Loading results...")
                review_target = state.analysis_phase  # "flash" or "pro"
                output_dir = f"./output/analysis_{review_target}"
                review_results_path = Path(output_dir) / "review_results.json"

                rework_queue: list[str] = []
                if review_results_path.exists():
                    try:
                        review_data = json.loads(review_results_path.read_text(encoding="utf-8"))
                        state.review_results.update(review_data)

                        for instance_id, result in review_data.items():
                            if not result.get("passed", False):
                                rework_queue.append(instance_id)

                        logging.info(
                            "Loaded review results for %s: %d passed, %d failed",
                            review_target,
                            len(review_data) - len(rework_queue),
                            len(rework_queue),
                        )
                    except Exception as exc:
                        logging.error("Failed to load review results: %s", exc)
                        # Fallback: all cases need rework
                        per_case_dir = Path(output_dir) / "per_case"
                        if per_case_dir.exists():
                            rework_queue = [f.stem for f in per_case_dir.glob("*.json")]
                else:
                    logging.warning("No review_results.json found at %s", review_results_path)
                    # Fallback to programmatic review
                    instances_to_review = state.rework_queue if state.rework_queue else None
                    rework_queue, review_results = review_all_rules(
                        output_dir, instance_ids=instances_to_review
                    )
                    state.review_results.update(review_results)
                    save_state(state)

                if not rework_queue:
                    logging.info("All rules passed LLM review for %s phase.", review_target)
                    state.review_phase = "done"
                    state.rework_queue = []
                    save_state(state)
                    continue  # Next loop will handle phase transition

                # Filter out cases that have exceeded max rework attempts
                final_queue = []
                for instance_id in rework_queue:
                    attempts = state.rework_attempts.get(instance_id, 0)
                    if attempts < RULE_REWORK_MAX_ATTEMPTS:
                        final_queue.append(instance_id)
                        logging.info(
                            "Queueing %s for rework (attempt %d/%d)",
                            instance_id, attempts + 1, RULE_REWORK_MAX_ATTEMPTS,
                        )
                    else:
                        logging.warning(
                            "%s exceeded max rework attempts (%d). Keeping best result.",
                            instance_id, RULE_REWORK_MAX_ATTEMPTS,
                        )

                if not final_queue:
                    logging.info(
                        "No cases eligible for further rework. Moving on from %s review.",
                        review_target,
                    )
                    state.review_phase = "done"
                    state.rework_queue = []
                    save_state(state)
                    continue  # Next loop will handle phase transition

                state.rework_queue = final_queue
                state.review_phase = "reworking"
                save_state(state)
                # Fall through to rework handling below
            else:
                # Review session crashed or never started properly
                logging.warning(
                    "Review session not running and no end marker. Restarting..."
                )
                start_fn(state)
                continue

        if state.review_phase == "reworking":
            # Determine the output dir for the current analysis phase
            review_target = state.analysis_phase  # "flash" or "pro"
            output_dir = f"./output/analysis_{review_target}"

            # Check if there's an active rework session
            current_rework = getattr(main, "_current_rework_instance", None)
            if current_rework and not is_rework_complete(current_rework):
                logging.info(
                    "Rework in progress for %s. Next check in 60s.", current_rework
                )
                time.sleep(60)
                continue

            # Previous rework finished (or no current rework)
            if current_rework:
                state.rework_attempts[current_rework] = (
                    state.rework_attempts.get(current_rework, 0) + 1
                )
                main._current_rework_instance = None

                # If this case exceeded max attempts, remove from queue
                if state.rework_attempts.get(current_rework, 0) >= RULE_REWORK_MAX_ATTEMPTS:
                    if current_rework in state.rework_queue:
                        state.rework_queue.remove(current_rework)
                        logging.warning(
                            "%s exceeded max rework attempts (%d). Keeping best result.",
                            current_rework, RULE_REWORK_MAX_ATTEMPTS,
                        )

                save_state(state)

            # Start next rework if queue not empty
            started = False
            while state.rework_queue:
                next_instance = state.rework_queue[0]
                if state.rework_attempts.get(next_instance, 0) >= RULE_REWORK_MAX_ATTEMPTS:
                    state.rework_queue.pop(0)
                    logging.warning(
                        "%s exceeded max rework attempts. Skipping.", next_instance
                    )
                    continue

                state.rework_attempts[next_instance] = (
                    state.rework_attempts.get(next_instance, 0) + 1
                )
                save_state(state)
                main._current_rework_instance = next_instance
                model_name = _resolve_analysis_model(review_target)
                start_rework(next_instance, model_name, output_dir)
                started = True
                break

            if not started:
                # Queue empty (or all cases exceeded max attempts)
                logging.info("Rework queue empty. Re-running targeted LLM review.")
                state.review_phase = "reviewing"
                save_state(state)
                continue

        if state.review_phase == "done":
            if state.analysis_phase == "flash":
                logging.info("Flash review complete. Starting flash aggregation.")
                state.aggregation_phase = "flash"
                state.review_phase = None
                save_state(state)
                start_aggregation(state)
                continue
            elif state.analysis_phase == "pro":
                logging.info("Pro review complete. Starting pro aggregation.")
                state.aggregation_phase = "pro"
                state.review_phase = None
                save_state(state)
                start_aggregation(state)
                continue
            logging.info("Review phase complete. Exiting.")
            return 0

        # Re-evaluate mode every loop iteration (phase may have changed)
        in_checker = state.checker_phase == "running"
        in_aggregation = state.aggregation_phase in ("flash", "pro") and not in_checker
        in_review = state.review_phase in ("reviewing", "reworking") and not in_aggregation and not in_checker
        in_analysis = state.analysis_phase in ("flash", "pro") and not in_aggregation and not in_review and not in_checker
        if in_checker:
            active_session = CHECKER_TMUX_SESSION
            active_log = CHECKER_LOG
            start_fn = start_checker_eval
        elif in_aggregation:
            active_session = AGGREGATION_TMUX_SESSION
            active_log = AGGREGATION_LOG
            start_fn = start_aggregation
        elif in_review:
            active_session = REVIEW_TMUX_SESSION
            active_log = REVIEW_LOG
            start_fn = lambda s: start_review(s, instance_ids=s.rework_queue or None)
        elif in_analysis:
            active_session = ANALYSIS_TMUX_SESSION
            active_log = ANALYSIS_LOG
            start_fn = start_analysis
        else:
            active_session = BATCH_TMUX_SESSION
            active_log = MASTER_LOG
            start_fn = start_batch

        session_alive = _tmux_session_exists(active_session)

        if not session_alive:
            if in_checker:
                end_marker = "=== Checker eval end ==="
                mode_label = "Checker"
            elif in_aggregation:
                end_marker = "=== Aggregation end ==="
                mode_label = "Aggregation"
            elif in_review:
                end_marker = "=== Review end ==="
                mode_label = "Review"
            elif in_analysis:
                end_marker = "=== Analysis end ==="
                mode_label = "Analysis"
            else:
                end_marker = "=== Batch end ==="
                mode_label = "Batch"
            logging.warning(
                "%s session not found. Polling log for end marker (up to %ds)...",
                mode_label,
                LOG_SETTLE_MAX_SECONDS,
            )
            log_analysis = analyze_recent_logs(active_log)
            settle_waited = 0
            while (
                not log_analysis["batch_completed"]
                and settle_waited < LOG_SETTLE_MAX_SECONDS
            ):
                time.sleep(LOG_SETTLE_POLL_SECONDS)
                settle_waited += LOG_SETTLE_POLL_SECONDS
                log_analysis = analyze_recent_logs(active_log)

            if log_analysis["api_auth_failed"]:
                enter_fatal(state, "DeepSeek API authentication failed (401). Check DEEPSEEK_API_KEY.")
                continue

            if log_analysis["api_rate_limited"]:
                enter_api_cooldown(state, "DeepSeek API transient error (rate-limit/network/5xx).")
                continue

            if not in_checker and not in_aggregation and not in_analysis and not in_review and log_analysis["docker_down"]:
                state.docker_retry_count += 1
                if state.docker_retry_count > DOCKER_MAX_RETRIES:
                    enter_fatal(state, f"Docker daemon unreachable after {DOCKER_MAX_RETRIES} retries.")
                    continue
                wait_sec = docker_backoff_wait(state.docker_retry_count)
                logging.warning(
                    "Docker daemon not running (retry %d/%d). Waiting %d min...",
                    state.docker_retry_count, DOCKER_MAX_RETRIES, wait_sec // 60,
                )
                save_state(state)
                time.sleep(wait_sec)
                continue

            # Fallback for checker: if results.json exists, treat as complete
            # even if the end marker wasn't captured in the log yet.
            if in_checker:
                checker_results = Path("output/checker_eval/pro_python/results.json")
                if checker_results.exists():
                    try:
                        data = json.loads(checker_results.read_text(encoding="utf-8"))
                        if data.get("metrics", {}).get("total", 0) > 0:
                            logging.info("Checker eval complete (results.json found).")
                            state.checker_phase = "done"
                            save_state(state)
                            continue
                    except Exception:
                        pass

            if log_analysis["batch_completed"]:
                if in_checker:
                    logging.info("Checker eval ended normally (%s).", end_marker)
                    state.checker_phase = "done"
                    save_state(state)
                    continue
                elif in_aggregation:
                    # Handled in the dedicated aggregation block above;
                    # this path should rarely be reached.
                    logging.info("Aggregation ended normally (%s).", end_marker)
                    continue
                elif in_analysis:
                    logging.info("Analysis ended normally (%s).", end_marker)
                    if state.analysis_phase == "flash":
                        if enable_review:
                            logging.info("Flash analysis complete. Starting flash review.")
                            state.review_phase = "reviewing"
                            save_state(state)
                            continue
                        else:
                            logging.info("Flash analysis complete. Starting flash aggregation.")
                            state.aggregation_phase = "flash"
                            save_state(state)
                            start_aggregation(state)
                            continue
                    elif state.analysis_phase == "pro":
                        if enable_review:
                            logging.info("Pro analysis complete. Starting pro review.")
                            state.review_phase = "reviewing"
                            save_state(state)
                            continue
                        else:
                            logging.info("Pro analysis complete. Starting pro aggregation.")
                            state.aggregation_phase = "pro"
                            save_state(state)
                            start_aggregation(state)
                            continue
                elif in_review:
                    # Review batch completed — handled above in the review_phase block
                    continue
                else:
                    logging.info("Batch ended normally (=== Batch end ===). Marking complete.")
                    state.status = "completed"
                    save_state(state)
                    if _should_start_analysis_after_batch():
                        logging.info("Batch complete. Starting flash analysis phase.")
                        state.analysis_phase = "flash"
                        state.analysis_completed = 0
                        save_state(state)
                        start_analysis(state)
                    else:
                        logging.info("Batch complete. No post-batch analysis configured for this dataset.")
                    continue

            # Unknown failure
            master_tail = log_analysis["master_tail"]
            error_lines = [ln for ln in master_tail.splitlines() if "ERROR" in ln or "error" in ln.lower()][-10:]
            if not error_lines:
                logging.warning(
                    "%s session gone with no end marker and no error lines. "
                    "Restarting (idempotent) instead of invoking Claude repair.",
                    mode_label,
                )
                start_fn(state)
                continue

            if state.claude_repair_count >= MAX_REPAIR_ATTEMPTS:
                logging.error(
                    "Claude repair exceeded max attempts (%d). Entering long cooldown.",
                    MAX_REPAIR_ATTEMPTS,
                )
                enter_api_cooldown(
                    state,
                    f"Repair limit reached ({MAX_REPAIR_ATTEMPTS}). Cooling down before retry.",
                    duration_seconds=REPAIR_BACKOFF_SECONDS,
                )
                continue

            logging.error("Unknown %s failure. Invoking Claude repair.", mode_label.lower())
            invoke_claude_repair(state, error_lines)
            continue

        # Session is alive — check for hang
        if is_log_stale(active_log):
            if in_checker:
                hang_label = "Checker"
            elif in_aggregation:
                hang_label = "Aggregation"
            elif in_review:
                hang_label = "Review"
            elif in_analysis:
                hang_label = "Analysis"
            else:
                hang_label = "Batch"
            logging.warning("%s appears hung. Restarting...", hang_label)
            _kill_tmux_session(active_session)
            time.sleep(5)
            start_fn(state)
            continue

        # Update progress
        log_analysis = analyze_recent_logs(active_log)
        if log_analysis["current_instance"]:
            state.current_instance = log_analysis["current_instance"]
        disk_completed = state.completed if not in_analysis else state.analysis_completed
        log_completed = log_analysis.get("log_completed", 0)
        if in_analysis:
            state.analysis_completed = max(disk_completed, log_completed)
        else:
            state.completed = max(disk_completed, log_completed)

        if not in_checker and not in_analysis and not in_review and state.total_instances > 0 and state.completed >= state.total_instances:
            logging.info("All %d instances appear complete.", state.total_instances)
            state.status = "completed"
            save_state(state)
            continue

        # Periodic Docker cleanup (only relevant during batch)
        if not in_checker and not in_analysis and not in_review:
            check_counter = getattr(main, "_check_counter", 0)
            check_counter += 1
            main._check_counter = check_counter
            if check_counter % DOCKER_PRUNE_INTERVAL_CHECKS == 0:
                cleanup_docker()

        if in_checker:
            phase_label = "checker"
            completed = 0  # Checker progress tracked externally via results.json
        elif in_aggregation:
            phase_label = f"aggregation({state.aggregation_phase})"
            completed = 0  # Aggregation progress not tracked via state fields
        elif in_review:
            phase_label = f"review({state.analysis_phase})"
            completed = 0  # Review progress not tracked via state fields
        elif in_analysis:
            phase_label = state.analysis_phase or "analysis"
            completed = state.analysis_completed
        else:
            phase_label = "batch"
            completed = state.completed

        logging.info(
            "Healthy: %s completed=%d current=%s",
            phase_label,
            completed,
            state.current_instance or "—",
        )
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.info("Watchdog stopped by user.")
        sys.exit(0)
