#!/usr/bin/env python3
"""Compute-node transport check; no GEPA state or scientific inputs are edited.

Two prepared images must pass no-model checks before at most two tiny real
Codex calls. Slurm owns the deadline. Use a new, dedicated output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.optimization.codex_cli_runtime import (  # noqa: E402
    CodexSIFExecution, _isolated_codex_environment, _runtime_preflight,
    _codex_binary, run_codex_json_agent,
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def verify(role, boundary, model, attempt, *, infer):
    if not infer:
        with _isolated_codex_environment(model) as environment:
            with boundary.launch(model, environment, attempt) as (prefix, env, effective, _):
                checks = _runtime_preflight(
                    effective, working_directory=boundary.cwd, environment=env,
                    command_prefix=prefix,
                    hidden_host_paths=(boundary.evidence_dir, boundary.evidence_dir.parent, attempt),
                )
                command = [*prefix, _codex_binary(effective), "sandbox",
                           "--permission-profile", ":read-only", "--cd", str(boundary.cwd),
                           "/bin/sh", "-c",
                           'test "$HOME" = /agent-home || exit 20; '
                           'test -r /evidence/manifest.json || exit 21; '
                           'test -x /opt/vibe-codex/codex-resources/bwrap || exit 22; '
                           'if touch /evidence/unexpected-write 2>/dev/null; then exit 23; fi; '
                           'test -x "$1" || exit 24; '
                           '"$1" --version || exit 25; printf "Read-only tool execution verified\\n"',
                           "startup-verification",
                           "/opt/miniconda3/envs/testbed/bin/python" if role == "reflector" else "/usr/local/bin/python3"]
                completed = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
                checks.append({"check": "sandbox_tools_home_environment_readonly",
                               "returncode": completed.returncode,
                               "stdout": completed.stdout, "stderr": completed.stderr})
                write_json(attempt / "preflight.json", checks)
                if completed.returncode:
                    raise RuntimeError(f"{role} substantive sandbox check failed: {completed.stderr}")
                return checks
    interpreter = "/opt/miniconda3/envs/testbed/bin/python" if role == "reflector" else "/usr/local/bin/python3"
    result, trajectory = run_codex_json_agent(
        container=boundary, model_config=model, working_directory=boundary.evidence_dir,
        attempt_dir=attempt, system="You are verifying a fresh Codex session inside a task-scoped SIF.",
        task="Read the probe file with a shell tool and return the observed values as JSON.",
        user=(f"Use {interpreter} to read /evidence/manifest.json and print its contents. "
              "Return ONLY a JSON object with the nonce and role values read from that file. "
              "Do not inspect credentials or other files."),
        evidence_manifest_path=boundary.evidence_dir / "manifest.json",
    )
    write_json(attempt / "trajectory.json", trajectory)
    expected = json.loads((boundary.evidence_dir / "manifest.json").read_text())
    if result != expected:
        raise RuntimeError(f"{role} final response did not match the probe file")
    events = next(item["content"]["events"] for item in trajectory if item["role"] == "codex_cli")
    tool_reads = [event.get("item", {}) for event in events if event.get("type") == "item.completed"]
    if not any(item.get("type") == "command_execution" and item.get("exit_code") == 0
               and expected["nonce"] in item.get("aggregated_output", "") for item in tool_reads):
        raise RuntimeError(f"{role} did not record a successful shell-tool read of the probe")
    return {"status": "passed", "shell_tool_read": True, "raw_final_json": "preserved"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-sif", type=Path, required=True)
    parser.add_argument("--evidence-sif", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--codex-binary", required=True)
    parser.add_argument("--codex-auth-file", default="${HOME}/.codex/auth.json")
    parser.add_argument("--model", default="gpt-6-sol")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    model = {"executor": "codex_cli", "codex_binary": args.codex_binary,
             "codex_auth_file": args.codex_auth_file, "codex_version": "0.155.1",
             "model": args.model, "reasoning_effort": args.reasoning_effort}
    report = {"status": "running", "job_id": os.environ.get("SLURM_JOB_ID"),
              "model": args.model, "reasoning_effort": args.reasoning_effort,
              "preflight_only": args.preflight_only, "codex_sessions_attempted": 0,
              "runtime_sha256": hashlib.sha256((Path(__file__).resolve().parents[2] /
                  "src/optimization/codex_cli_runtime.py").read_bytes()).hexdigest(), "roles": {}}
    try:
        with tempfile.TemporaryDirectory(prefix="vibe-codex-startup-") as temporary:
            root = Path(temporary)
            boundaries = {}
            for role, sif in (("reflector", args.case_sif), ("curator", args.evidence_sif)):
                evidence = root / role / "evidence"
                evidence.mkdir(parents=True)
                write_json(evidence / "manifest.json", {"nonce": secrets.token_hex(16), "role": role})
                repository = None
                if role == "reflector":
                    repository = root / role / "repository"
                    repository.mkdir()
                    (repository / "README.md").write_text("Synthetic transport probe only.\n")
                boundary = CodexSIFExecution(sif, evidence, repository)
                attempt = args.output_dir / role
                attempt.mkdir()
                boundaries[role] = (boundary, attempt)
                report["roles"][role] = {"sif_path": str(sif), "preflight":
                    verify(role, boundary, model, attempt, infer=False)}
                write_json(args.output_dir / "report.json", report)
            if not args.preflight_only:
                for role, (boundary, attempt) in boundaries.items():
                    report["codex_sessions_attempted"] += 1
                    write_json(args.output_dir / "report.json", report)
                    report["roles"][role]["inference"] = verify(role, boundary, model, attempt, infer=True)
                    write_json(args.output_dir / "report.json", report)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        if hasattr(exc, "trajectory"):
            write_json(args.output_dir / "failure_trajectory.json", exc.trajectory)
        raise
    finally:
        write_json(args.output_dir / "report.json", report)
        print(json.dumps({"status": report["status"], "output_dir": str(args.output_dir)}), flush=True)


if __name__ == "__main__":
    main()
