"""Real CLI refresh lifecycle against a loopback, rotating synthetic issuer.

No real login, OAuth endpoint, model request, or production runtime mutation.
The issuer's single-use rotation is a fault-injection assumption, not a
measurement of OpenAI's token-rotation policy.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs

from tests.fixtures import codex_auth_63a799f as legacy


def _jwt():
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    return encode({"alg": "none"}) + "." + encode({
        "email": "synthetic@example.invalid", "sub": "synthetic-user",
        "exp": int(time.time()) + 86400,
        "https://api.openai.com/auth": {"chatgpt_account_id": "synthetic-account",
                                      "chatgpt_plan_type": "pro"},
    }) + ".synthetic"


def _tokens(generation):
    return {"id_token": _jwt(), "access_token": f"synthetic-access-{generation}",
            "refresh_token": f"synthetic-refresh-{generation}",
            "account_id": "synthetic-account"}


@contextmanager
def rotating_issuer():
    state = {"generation": 0, "requests": []}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {key: values[0] for key, values in parse_qs(body.decode()).items()}
            with lock:
                accepted = (self.path == "/token" and
                            payload.get("refresh_token") == f"synthetic-refresh-{state['generation']}")
                if accepted:
                    state["generation"] += 1
                    reply = dict(_tokens(state["generation"]), token_type="Bearer", expires_in=86400)
                else:
                    reply = {"error": {"code": "refresh_token_reused", "message": "Synthetic refresh token was reused"}}
                status = 200 if accepted else 401
                state["requests"].append({"path": self.path, "status": status})
            encoded = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def refresh_with_real_cli(binary, home, issuer, *, force=True):
    """Ask the CLI's managed-auth implementation to refresh; no inference."""
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": str(home), "CODEX_HOME": str(home),
                   "CODEX_REFRESH_TOKEN_URL_OVERRIDE": issuer + "/token",
                   "CODEX_AUTHAPI_BASE_URL": issuer,
                   "NO_PROXY": "127.0.0.1,localhost", "RUST_LOG": "error"}
    # app-server has no exec-only ignore flags; HOME, CODEX_HOME and cwd all
    # point to the synthetic private directory, with no user configuration.
    command = [binary, "app-server",
               "--listen", "stdio://", "-c", 'cli_auth_credentials_store="file"',
               "-c", 'analytics.enabled=false', "-c", "chatgpt_base_url=" + json.dumps(issuer),
               "-c", 'model_provider="synthetic_probe"', "-c",
               'model_providers.synthetic_probe={name="synthetic-auth-only",wire_api="responses",'
               'requires_openai_auth=true,base_url=' + json.dumps(issuer + "/v1") + '}']
    lines = queue.Queue()
    with tempfile.TemporaryFile(mode="w+") as stderr:
        process = subprocess.Popen(command, cwd=home, env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1)
        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def send(value):
            process.stdin.write(json.dumps(value) + "\n")
            process.stdin.flush()
        def response(identifier):
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                if line is None:
                    stderr.seek(0)
                    raise RuntimeError("Synthetic app-server exited before its response: " + stderr.read()[-1800:])
                message = json.loads(line)
                if message.get("id") == identifier:
                    return message
            raise TimeoutError("Synthetic app-server response timeout")
        try:
            send({"id": 0, "method": "initialize", "params": {
                "clientInfo": {"name": "vibe_synthetic_auth_probe", "version": "1"}}})
            initialized = response(0)
            if "error" in initialized:
                raise RuntimeError("Synthetic app-server initialization rejected: " + json.dumps(initialized))
            send({"method": "initialized"})
            send({"id": 1, "method": "account/read", "params": {"refreshToken": force}})
            result = response(1)
            value = json.loads((home / "auth.json").read_text())
            return {"response_has_error": "error" in result,
                    "error": result.get("error"),
                    "account_present": result.get("result", {}).get("account") is not None,
                    "private_refresh_generation": int(value["tokens"]["refresh_token"].rsplit("-", 1)[1])}
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
            reader.join(timeout=4)
            process.stdout.close()


def run_probe(binary, root):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = root / "synthetic-auth.json"
    seed = json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                       "tokens": _tokens(0), "last_refresh": datetime.now(timezone.utc).isoformat()})
    source.write_text(seed)
    source.chmod(0o600)
    config = {"codex_auth_file": str(source)}
    report = {"real_credentials_used": False, "model_calls": 0,
              "issuer": "loopback_single_use_rotation_fault_injection",
              "scope": "refresh_transport_and_disk_persistence_not_workspace_routing"}
    def call(issuer, barrier=None, *, force=True):
        with legacy._isolated_codex_environment(config) as environment:
            if barrier is not None:
                barrier.wait(timeout=8)
            return refresh_with_real_cli(binary, Path(environment["CODEX_HOME"]), issuer, force=force)
    with rotating_issuer() as (issuer, state):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call, issuer, barrier, force=False) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        report["concurrent_without_refresh"] = {"calls": results, "issuer_requests": state["requests"],
                                                 "source_unchanged": source.read_text() == seed}
        assert not state["requests"]
        assert all(row["private_refresh_generation"] == 0 for row in results)
    with rotating_issuer() as (issuer, state):
        results = [call(issuer), call(issuer)]
        report["sequential_legacy"] = {"calls": results, "issuer_requests": state["requests"],
                                       "source_unchanged": source.read_text() == seed}
    with rotating_issuer() as (issuer, state):
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call, issuer, barrier) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        report["concurrent_legacy"] = {"calls": results, "issuer_requests": state["requests"],
                                       "source_unchanged": source.read_text() == seed}
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    report["cli_version"] = version.stdout.strip()
    for name in ("sequential_legacy", "concurrent_legacy"):
        case = report[name]
        assert [row["status"] for row in case["issuer_requests"]] == [200, 401]
        assert sorted(row["private_refresh_generation"] for row in case["calls"]) == [0, 1]
        assert case["source_unchanged"]
    report["risk_assertions_passed"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="synthetic-codex-refresh-") as directory:
        rendered = json.dumps(run_probe(args.binary, directory), indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x") as handle:
                handle.write(rendered)
        print(rendered, end="")
