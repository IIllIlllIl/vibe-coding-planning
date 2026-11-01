"""Structured audit and API usage logging for GEPA runs."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.artifact_policy import mark_artifact_path

_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


@contextmanager
def optional_diagnostic(label: str):
    """A derived log/export cannot decide whether scientific work succeeded.

    Use only around work with no downstream experiment consumer. Do not wrap
    Agent execution, evidence, checkpoints, policy decisions or task state.
    Control-flow BaseExceptions (including ControllerYield) still propagate.
    """
    try:
        yield
    except Exception as exc:
        try:
            logging.getLogger(__name__).warning("%s unavailable (%s)", label, type(exc).__name__)
        except Exception:
            pass


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def diagnostic_print(*args, **kwargs):
    """Console-only output; never use for a machine-readable IPC response."""
    with optional_diagnostic("diagnostic console"):
        print(*args, **kwargs)


def audit_record(value: Any) -> Any:
    """Make a JSON-safe copy without filtering scientific or diagnostic text.

    Credentials belong in private provider state, outside Agent inputs. Review
    private result logs after execution; never silently rewrite evidence or
    reject a completion because its text resembles a credential.
    """
    def preserve(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(key): preserve(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [preserve(child) for child in item]
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        return str(item)

    return preserve(value)


class JsonlLogger:
    """Thread-safe append-only JSONL logger."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Construction must not perform I/O: optional diagnostics may be
        # unavailable without preventing a scientific operation from starting.
        resolved = path.absolute()
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(resolved, threading.Lock())
        self._prepared = False

    def write(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        with self._lock:
            if not self._prepared:
                mark_artifact_path(self.path)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._prepared = True
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n"
                )

    def write_optional(self, event: str, **fields: Any) -> None:
        """Best-effort diagnostics, never an input to experiment decisions."""
        with optional_diagnostic(str(self.path)):
            self.write(event, **fields)


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
            self._logger.write_optional(
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
        self._logger.write_optional(
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
