"""No-model acceptance: real Codex tools against a loopback scripted provider.

No credentials or external model endpoints are used. The first synthetic shell
contains an unreachable pip invocation and a marker; the marker must never be
created. The second command must run in the same Codex turn after the denial.
This is a transport test fixture, not an experimental Agent implementation.
"""
from __future__ import annotations

import argparse
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading

from src.agents.codex_hooks import (
    AUDIT_FILE, SUBMISSION_AUDIT_FILE, hook_options, write_bundle,
    validate_native_commands, validate_native_submission,
)


def verify(binary: str, root: Path, policy_bundle: Path | None = None, *, submission=False):
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-policy-", dir=root) as temporary:
        directory = Path(temporary)
        workspace = directory / "workspace"
        workspace.mkdir()
        home = directory / "private-cli"
        home.mkdir(mode=0o700)
        policy = directory / "policy"
        context = {"prompt_urls": [], "context": {"phase": "synthetic_acceptance"}}
        if submission:
            context["submission_contract"] = {"phase": "plan", "allow_empty": False, "workdir": str(workspace)}
        if policy_bundle is None:
            write_bundle(policy, context)
        else:
            policy.mkdir()
            shutil.copyfile(policy_bundle, policy / "policy.pyz")
            (policy / "context.json").write_text(json.dumps(context))
        blocked = workspace / "forbidden-marker"
        corrected = workspace / "corrected-marker"
        requests = []
        errors = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    if self.headers.get("Content-Encoding") == "gzip": body = gzip.decompress(body)
                    value = json.loads(body)
                    requests.append(value)
                    index = len(requests)
                    if index > (4 if submission else 3): raise ValueError("unexpected extra model request")
                    if index < 3:
                        names = []
                        def names_from(tools):
                            for tool in tools:
                                if tool.get("type") == "function": names.append(tool["name"])
                                names_from(tool.get("tools", []))
                        names_from(value.get("tools", []))
                        name = next((n for n in names if n in {"exec_command", "shell_command", "shell"}), "exec_command")
                        command = ("if false; then pip install synthetic-policy-probe; fi; "
                                   "printf forbidden > " + shlex.quote(str(blocked))) if index == 1 else (
                                   "printf corrected > " + shlex.quote(str(corrected)))
                        args = ({"cmd": command, "workdir": str(workspace)} if name == "exec_command" else
                                {"command": command, "workdir": str(workspace)} if name == "shell_command" else
                                {"command": ["bash", "-lc", command], "workdir": str(workspace)})
                        item = {"id": f"fc_{index}", "type": "function_call", "call_id": f"call_{index}",
                                "name": name, "arguments": json.dumps(args)}
                    else:
                        text = ("START_PLAN\n# Plan\nEND_PLAN" if index == 3 else
                                "START_PLAN\n# Plan\nInspect the relevant code and preserve existing behavior.\nEND_PLAN\n") if submission else "POLICY_PROBE_DONE"
                        item = {"id": "msg_final", "type": "message", "role": "assistant", "status": "completed",
                                "content": [{"type": "output_text", "text": text, "annotations": []}]}
                    events = [
                        {"type": "response.created", "response": {"id": f"r{index}", "status": "in_progress"}},
                        {"type": "response.output_item.added", "output_index": 0, "item": item},
                        {"type": "response.output_item.done", "output_index": 0, "item": item},
                        {"type": "response.completed", "response": {"id": f"r{index}", "status": "completed", "output": [item],
                         "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
                    ]
                    payload = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except Exception as exc:
                    errors.append(str(exc))
                    self.send_error(500)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        provider = ('{name="loopback-policy-probe",base_url="http://127.0.0.1:' + str(server.server_port) +
                    '/v1",wire_api="responses",requires_openai_auth=false,supports_websockets=false}')
        options = hook_options(sys.executable, directory=str(policy), audit_directory=str(directory), submission=submission)
        command = [binary, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   "--skip-git-repo-check", "--sandbox", "workspace-write", "--json", "--model", "gpt-6-sol",
                   "--cd", str(workspace), "--config", 'model_provider="policy_probe"',
                   "--output-last-message", str(directory / "final.txt"),
                   "--config", "model_providers.policy_probe=" + provider,
                   "--config", "features.enable_request_compression=false", *options,
                   "Synthetic local transport acceptance. Follow the scripted tool calls."]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "CODEX_HOME": str(home),
               "TMPDIR": str(directory), "NO_PROXY": "127.0.0.1,localhost"}
        try:
            result = subprocess.run(command, input="", capture_output=True, text=True, env=env, timeout=50)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        report = {"cli_returncode": result.returncode, "requests": len(requests), "server_errors": errors,
                  "events": events, "native_error_events": [e for e in events if e.get("type") in {"error", "turn.failed"}],
                  "forbidden_marker_exists": blocked.exists(), "corrected_marker_exists": corrected.exists(),
                  "denial_returned_to_native_loop": len(requests) >= 2 and "returncode=126" in json.dumps(requests[1]),
                  "stderr": result.stderr[-2200:]}
        try:
            records = validate_native_commands([{"role":"codex_cli","content":{"events":events}}],
                directory / AUDIT_FILE, context=context, source_log=directory / "sources.jsonl")
            report["policy_receipts"] = records
            if submission:
                report["submission_receipts"] = validate_native_submission((directory / "final.txt").read_text(),
                    directory / SUBMISSION_AUDIT_FILE, contract=context["submission_contract"], output_dir=directory)
                report["submission_feedback_returned"] = len(requests) == 4 and "Host rejected the submission" in json.dumps(requests[3])
        except Exception as exc:
            report["audit_error"] = str(exc)
        if not (result.returncode == 0 and not report["native_error_events"]
                and len(requests) == (4 if submission else 3) and not errors and not blocked.exists()
                and corrected.is_file() and report["denial_returned_to_native_loop"] and "audit_error" not in report
                and (not submission or report.get("submission_feedback_returned"))):
            report["events"] = events
            report["model_tool_observations"] = [r.get("input", [])[-3:] for r in requests[1:]]
            raise RuntimeError(json.dumps(report, indent=2))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-binary", required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--policy-bundle", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--submission", action="store_true", help="Also exercise invalid Plan -> Stop feedback -> corrected Plan")
    args = parser.parse_args()
    report = verify(args.codex_binary, args.work_root, args.policy_bundle, submission=args.submission)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
