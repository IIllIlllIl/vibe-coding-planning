"""Test-only OpenAI-compatible LLM stub for GEPA monitoring.

The server is intentionally small and local-only. It lets GEPA exercise the
real mini-swe-agent/LiteLLM HTTP path without sending requests to DeepSeek.
Every request is appended to a JSONL log, and expensive/unsupported model names
are rejected before a fake chat-completion response is returned.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import request


DEFAULT_ALLOWED_MODELS = ("deepseek-v4-flash", "deepseek/deepseek-v4-flash")
DEFAULT_FORBIDDEN_MARKERS = ("pro", "kimi", "anthropic")


@dataclass(frozen=True)
class LoggedRequest:
    model: str
    allowed: bool
    path: str
    blocked_reason: str | None


def _normalize_model(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def _message_summary(messages: list[dict[str, Any]]) -> dict[str, Any]:
    text = "\n".join(str(message.get("content", "")) for message in messages)
    return {
        "message_count": len(messages),
        "roles": [str(message.get("role", "")) for message in messages],
        "has_checker_result_path": "/tmp/gepa_checker_result.json" in text,
        "has_candidate_rules_path": "/tmp/candidate_rules.txt" in text,
        "has_candidate_rules_tag": "<candidate_rules>" in text,
        "content_chars": len(text),
    }


def _checker_submission() -> str:
    return """THOUGHT: Apply the candidate rules conservatively.

```bash
cat <<'EOF' > /tmp/gepa_checker_result.json
{
  "predicted_resolved": false,
  "decision_reason": "Fake LLM stub defaulted to unresolved for monitoring.",
  "repository_evidence": []
}
EOF
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat /tmp/gepa_checker_result.json
```"""


def _reflection_submission() -> str:
    return """THOUGHT: Preserve a minimal deterministic candidate for monitoring.

```bash
cat <<'EOF' > /tmp/candidate_rules.txt
1. Predict resolved only when the supplied plan is explicitly supported by the candidate rules and observable repository evidence.
2. If the rules do not clearly support predicting resolved, predict unresolved.
EOF
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat /tmp/candidate_rules.txt
```"""


def _generic_submission() -> str:
    return """THOUGHT: Return a harmless command for the fake LLM server.

```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
```"""


def _completion_content(messages: list[dict[str, Any]]) -> str:
    text = "\n".join(str(message.get("content", "")) for message in messages)
    if "/tmp/gepa_checker_result.json" in text:
        return _checker_submission()
    if "/tmp/candidate_rules.txt" in text:
        return _reflection_submission()
    return _generic_submission()


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class FakeLLMServer:
    """Own a local HTTP server that mimics a chat-completions endpoint."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        allowed_models: tuple[str, ...] = DEFAULT_ALLOWED_MODELS,
        forbidden_markers: tuple[str, ...] = DEFAULT_FORBIDDEN_MARKERS,
        log_path: Path,
    ) -> None:
        self.host = host
        self.port = port
        self.allowed_models = allowed_models
        self.forbidden_markers = forbidden_markers
        self.log_path = log_path
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return f"http://{self.host}:{self.port}"
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    self._send_json(200, {"ok": True})
                    return
                self._send_json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    self._send_json(400, {"error": {"message": "invalid JSON"}})
                    return

                model = str(payload.get("model", ""))
                messages = payload.get("messages", [])
                if not isinstance(messages, list):
                    messages = []
                allowed, reason = server._model_allowed(model)
                server._log_request(
                    path=self.path,
                    model=model,
                    payload=payload,
                    allowed=allowed,
                    blocked_reason=reason,
                )
                if not allowed:
                    self._send_json(
                        400,
                        {
                            "error": {
                                "message": reason or "model blocked",
                                "type": "blocked_model",
                            }
                        },
                    )
                    return

                content = _completion_content(messages)
                prompt_tokens = max(1, sum(len(json.dumps(item)) for item in messages) // 4)
                completion_tokens = max(1, len(content) // 4)
                self._send_json(
                    200,
                    {
                        "id": f"fake-chatcmpl-{int(time.time() * 1000)}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": content,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    },
                )

            def _send_json(self, status: int, value: dict[str, Any]) -> None:
                data = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="fake-litellm-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> FakeLLMServer:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    def _model_allowed(self, model: str) -> tuple[bool, str | None]:
        lowered = model.lower()
        for marker in self.forbidden_markers:
            if marker.lower() in lowered:
                return False, f"blocked model marker {marker!r} in {model!r}"
        normalized = _normalize_model(model)
        if model not in self.allowed_models and normalized not in self.allowed_models:
            return False, f"model {model!r} is not in the allowlist"
        return True, None

    def _log_request(
        self,
        *,
        path: str,
        model: str,
        payload: dict[str, Any],
        allowed: bool,
        blocked_reason: str | None,
    ) -> None:
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            messages = []
        _write_jsonl(
            self.log_path,
            {
                "timestamp": time.time(),
                "event": "fake_llm_request",
                "path": path,
                "model": model,
                "normalized_model": _normalize_model(model),
                "allowed": allowed,
                "blocked_reason": blocked_reason,
                **_message_summary(messages),
            },
        )


def read_logged_requests(path: Path) -> list[LoggedRequest]:
    if not path.is_file():
        return []
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        LoggedRequest(
            model=str(record["model"]),
            allowed=bool(record["allowed"]),
            path=str(record["path"]),
            blocked_reason=record.get("blocked_reason"),
        )
        for record in records
    ]


def post_chat_completion(
    api_base: str,
    *,
    model: str,
    messages: list[dict[str, Any]],
) -> tuple[int, dict[str, Any]]:
    data = json.dumps({"model": model, "messages": messages}).encode("utf-8")
    req = request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=5) as response:  # noqa: S310
            return response.status, json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        if hasattr(exc, "read") and hasattr(exc, "code"):
            return int(exc.code), json.loads(exc.read().decode("utf-8"))
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a local test-only OpenAI-compatible LLM stub."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--allow-model",
        action="append",
        default=list(DEFAULT_ALLOWED_MODELS),
        help="Allowed model name. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--forbid-marker",
        action="append",
        default=list(DEFAULT_FORBIDDEN_MARKERS),
        help="Case-insensitive forbidden model substring.",
    )
    parser.add_argument(
        "--log-jsonl",
        type=Path,
        default=Path("/tmp/gepa_fake_llm_calls.jsonl"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    server = FakeLLMServer(
        host=args.host,
        port=args.port,
        allowed_models=tuple(args.allow_model),
        forbidden_markers=tuple(args.forbid_marker),
        log_path=args.log_jsonl,
    )
    with server:
        print(f"fake LLM server listening at {server.url}", flush=True)
        print(f"logging requests to {args.log_jsonl}", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("stopping fake LLM server", flush=True)


if __name__ == "__main__":
    main()
