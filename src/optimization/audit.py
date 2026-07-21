"""Structured audit and API usage logging for GEPA runs."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "password", "secret", "token")


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def redact_sensitive(value: Any) -> Any:
    """Return a JSON-safe copy with credential-shaped values removed."""
    secrets = tuple(
        sorted(
            {
                item
                for name, item in os.environ.items()
                if item
                and any(part in name.lower() for part in _SENSITIVE_KEY_PARTS)
            },
            key=len,
            reverse=True,
        )
    )

    def redact(item: Any) -> Any:
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key_text = str(key)
                if any(
                    part in key_text.lower() for part in _SENSITIVE_KEY_PARTS
                ):
                    result[key_text] = "[REDACTED]"
                else:
                    result[key_text] = redact(child)
            return result
        if isinstance(item, (list, tuple)):
            return [redact(child) for child in item]
        if isinstance(item, str):
            result = item
            for secret in secrets:
                result = result.replace(secret, "[REDACTED]")
            return result
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return str(item)

    return redact(value)


class JsonlLogger:
    """Thread-safe append-only JSONL logger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve()
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(resolved, threading.Lock())

    def write(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                + "\n"
            )


def _usage_value(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return 0


class AuditedModel:
    """Proxy a mini-swe-agent model and log each API response usage."""

    def __init__(
        self,
        model: Any,
        logger: JsonlLogger,
        *,
        phase: str,
        context: dict[str, Any],
    ) -> None:
        self._model = model
        self._logger = logger
        self._phase = phase
        self._context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    def query(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        started = time.monotonic()
        cost_before = float(getattr(self._model, "cost", 0.0))
        try:
            response = self._model.query(messages, **kwargs)
        except Exception as exc:
            self._logger.write(
                "model_call",
                phase=self._phase,
                duration_seconds=time.monotonic() - started,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                **self._context,
            )
            raise
        raw = response.get("extra", {}).get("response", {})
        usage = raw.get("usage") or {}
        cost_after = float(getattr(self._model, "cost", cost_before))
        prompt_tokens = _usage_value(
            usage,
            "prompt_tokens",
            "input_tokens",
        )
        completion_tokens = _usage_value(
            usage,
            "completion_tokens",
            "output_tokens",
        )
        total_tokens = _usage_value(usage, "total_tokens")
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        self._logger.write(
            "model_call",
            phase=self._phase,
            model=str(getattr(self._model.config, "model_name", "")),
            duration_seconds=time.monotonic() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            reported_cost_usd=max(0.0, cost_after - cost_before),
            success=True,
            response_id=raw.get("id"),
            provider_model=raw.get("model"),
            **self._context,
        )
        return response
