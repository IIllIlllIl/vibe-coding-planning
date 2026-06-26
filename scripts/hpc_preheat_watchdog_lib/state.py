from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Phase = Literal[
    "idle",
    "pilot_waiting",
    "pilot_failed",
    "agent_cooldown",
    "repair_validating",
    "pilot_completed",
    "full_preheat_submitted",
    "completed",
    "blocked",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WatchdogState:
    phase: Phase = "idle"
    pilot_job_id: str | None = None
    full_job_id: str | None = None
    pilot_stdout_path: str | None = None
    pilot_stderr_path: str | None = None
    full_stdout_path: str | None = None
    full_stderr_path: str | None = None
    expected_pilot_images: int = 0
    expected_full_images: int = 0
    last_sif_count: int = 0
    last_job_state: str | None = None
    repair_attempts: int = 0
    whitelist_violations: int = 0
    agent_cooldowns: int = 0
    cooldown_until: str | None = None
    last_error_class: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None
    last_action_at: str | None = None

    def touch_checked(self) -> None:
        self.last_checked_at = now_iso()

    def touch_action(self) -> None:
        self.last_action_at = now_iso()


def load_state(path: Path) -> WatchdogState:
    if not path.is_file():
        return WatchdogState()
    data = json.loads(path.read_text(encoding="utf-8"))
    defaults = asdict(WatchdogState())
    defaults.update(data)
    return WatchdogState(**defaults)


def save_state(path: Path, state: WatchdogState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True), encoding="utf-8")
