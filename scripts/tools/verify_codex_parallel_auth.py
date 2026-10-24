"""Two authorized ordinary calls using the old private-home design.

Reuse the established SIF startup verifier, transport and raw artifacts. No
forced refresh, source credential writes, scientific input, retry or workflow
controller. Any naturally changed private auth is retained privately.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def auth_freshness(source):
    value = json.loads(source.read_bytes())
    now = datetime.now(timezone.utc)
    last = datetime.fromisoformat(value["last_refresh"].replace("Z", "+00:00"))
    encoded = value["tokens"]["access_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    age = (now - last).total_seconds()
    remaining = claims["exp"] - now.timestamp()
    if (value.get("auth_mode") != "chatgpt" or not 0 <= age < 7 * 86400
            or remaining < 900 or stat.S_IMODE(source.stat().st_mode) != 0o600):
        raise RuntimeError("Auth freshness/permission prerequisite failed; no inference")
    if source.with_name("." + source.name + ".vibe-state").joinpath("lease.json").exists():
        raise RuntimeError("Existing authentication lease; no inference")
    return {"refresh_age_hours": round(age / 3600, 2),
            "access_remaining_seconds": int(remaining), "forced_refresh": False}


def run_probe(args):
    sys.path.insert(0, str(args.baseline_project))
    from src.optimization import codex_cli_runtime as runtime
    from scripts.tools import verify_codex_sif_startup as startup
    spec = importlib.util.spec_from_file_location("frozen_legacy_auth", args.legacy_auth_module)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    source = args.auth_source.expanduser().resolve(strict=True)
    freshness = auth_freshness(source)
    initial = source.read_bytes()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    model = {"executor": "codex_cli", "codex_binary": str(args.codex_binary),
             "codex_auth_file": str(source), "codex_version": args.codex_version,
             "model": "gpt-6-sol", "reasoning_effort": "high"}
    report = {"status": "running", "job_id": os.environ.get("SLURM_JOB_ID"),
              "auth_prerequisites": freshness, "model": model["model"],
              "reasoning_effort": "high", "cli_version": args.codex_version,
              "session_limit": 2, "roles": {}, "natural_refresh_copies_retained": 0}
    barrier = threading.Barrier(2)
    local = threading.local()
    guard = threading.Lock()
    homes, intervals = [], []
    original_run = subprocess.run
    def save_report():
        startup.write_json(args.output_dir / "report.json", report)
    @contextmanager
    def private_context(config):
        with legacy._isolated_codex_environment(config) as environment:
            home = Path(environment["CODEX_HOME"])
            with guard:
                homes.append(str(home))
            try:
                if getattr(local, "infer", False):
                    barrier.wait(timeout=30)
                yield environment
            finally:
                private = home / "auth.json"
                if private.is_file() and private.read_bytes() != initial:
                    destination = args.recovery_dir / secrets.token_hex(16)
                    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
                    destination.chmod(0o700)
                    shutil.copyfile(private, destination / "auth.json")
                    (destination / "auth.json").chmod(0o600)
                    with guard:
                        report["natural_refresh_copies_retained"] += 1
    def observed_run(command, *positional, **keywords):
        native = "/opt/vibe-codex/bin/codex"
        if (not getattr(local, "infer", False) or native not in command
                or command[command.index(native) + 1] != "exec"
                or "--help" in command or "-h" in command):
            return original_run(command, *positional, **keywords)
        started = time.monotonic()
        started_utc = datetime.now(timezone.utc).isoformat()
        try:
            return original_run(command, *positional, **keywords)
        finally:
            with guard:
                intervals.append({"role": local.role, "start": started, "end": time.monotonic(),
                                  "started_utc": started_utc})
    try:
        with tempfile.TemporaryDirectory(prefix="vibe-parallel-auth-evidence-") as temporary:
            calls = {}
            for role in ("parallel_0", "parallel_1"):
                evidence = Path(temporary) / role / "evidence"
                evidence.mkdir(parents=True)
                startup.write_json(evidence / "manifest.json", {"nonce": secrets.token_hex(16), "role": role})
                boundary = runtime.CodexSIFExecution(args.evidence_sif, evidence,
                    interpreter="/usr/local/bin/python3", tool_network_disabled=True)
                attempt = args.output_dir / role
                attempt.mkdir()
                calls[role] = (boundary, attempt)
            with patch.object(runtime, "_isolated_codex_environment", private_context), \
                 patch.object(startup, "_isolated_codex_environment", private_context), \
                 patch.object(subprocess, "run", observed_run):
                for role, (boundary, attempt) in calls.items():
                    local.role, local.infer = role, False
                    report["roles"][role] = {"preflight": startup.verify(role, boundary, model, attempt, infer=False)}
                    save_report()
                def infer(role):
                    local.role, local.infer = role, True
                    boundary, attempt = calls[role]
                    try:
                        return startup.verify(role, boundary, model, attempt, infer=True)
                    except Exception as error:
                        if hasattr(error, "trajectory"):
                            startup.write_json(attempt / "failure_trajectory.json", error.trajectory)
                        return {"status": "failed", "error_type": type(error).__name__, "error": str(error)}
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {role: executor.submit(infer, role) for role in calls}
                    for role, future in futures.items():
                        report["roles"][role]["inference"] = future.result()
                        save_report()
        report["source_unchanged"] = source.read_bytes() == initial
        report["private_homes_unique_and_cleaned"] = len(set(homes)) == len(homes) and all(not Path(h).exists() for h in homes)
        report["native_calls"] = len(intervals)
        report["native_exec_intervals"] = intervals
        report["native_overlap_seconds"] = round(max(0, min(i["end"] for i in intervals) - max(i["start"] for i in intervals)), 3) if len(intervals) == 2 else 0
        good = (len(intervals) == 2 and report["native_overlap_seconds"] > 0
                and report["source_unchanged"] and report["private_homes_unique_and_cleaned"]
                and all(r["inference"]["status"] == "passed" for r in report["roles"].values()))
        report["status"] = "passed" if good else "failed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error))
    finally:
        save_report()
        print(json.dumps({"status": report["status"], "output_dir": str(args.output_dir)}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline-project", "legacy-auth-module", "evidence-sif", "codex-binary", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--codex-version", default="0.160.1")
    parser.add_argument("--auth-source", type=Path, default=Path.home() / ".codex/auth.json")
    parser.add_argument("--recovery-dir", type=Path, default=Path.home() / ".config/vibe-coding-planning/auth-probe-recovery")
    args = parser.parse_args()
    report = run_probe(args)
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
