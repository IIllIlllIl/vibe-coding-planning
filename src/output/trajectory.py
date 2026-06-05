"""Trajectory file saving.

Saves agent message histories with metadata per §4.2 naming convention.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Valid trajectory roles per spec §4.2
VALID_ROLES = {"plan_gen", "code_gen", "reflect"}


def _sanitize_timestamp(dt: datetime) -> str:
    """Format a datetime as ISO 8601 compact: YYYYMMDDTHHMMSS."""
    return dt.strftime("%Y%m%dT%H%M%S")


def save_trajectory(
    messages: list[dict[str, Any]],
    *,
    round_num: int,
    role: str,
    output_dir: str | Path,
    extra_metadata: dict[str, Any] | None = None,
) -> Path:
    """Save a trajectory file with metadata.

    The file is named ``trajectory_{round_num}_{role}_{timestamp}.json``
    per the naming convention in requirement-document.md §4.2.

    Args:
        messages: The agent's message history (list of dicts with role/content/timestamp).
        round_num: The round number (1-indexed).
        role: One of ``plan_gen``, ``code_gen``, ``reflect``.
        output_dir: Directory where the trajectory file will be written.
        extra_metadata: Additional metadata fields to include.

    Returns:
        The path to the written trajectory file.

    Raises:
        ValueError: If ``role`` is not one of the valid roles.
    """
    if role not in VALID_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_ROLES))}"
        )

    if round_num < 1:
        raise ValueError(f"round_num must be >= 1, got {round_num}")

    now = datetime.now(timezone.utc)
    timestamp = _sanitize_timestamp(now)
    filename = f"trajectory_{round_num}_{role}_{timestamp}.json"

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filepath = output_path / filename

    payload: dict[str, Any] = {
        "round": round_num,
        "role": role,
        "timestamp": now.isoformat(),
        "messages": messages,
    }
    if extra_metadata:
        payload.update(extra_metadata)

    filepath.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return filepath


def validate_filename(filename: str) -> bool:
    """Validate that a trajectory filename matches the required format.

    Format: ``trajectory_{round_num}_{role}_{timestamp}.json``
    where timestamp is ISO 8601 compact (YYYYMMDDTHHMMSS).

    Args:
        filename: The filename to validate (not a full path).

    Returns:
        True if the filename matches the required format.
    """
    pattern = r"^trajectory_(\d+)_(plan_gen|code_gen|reflect)_(\d{8}T\d{6})\.json$"
    return bool(re.match(pattern, filename))


def parse_filename(filename: str) -> dict[str, int | str] | None:
    """Parse a trajectory filename and extract its components.

    Args:
        filename: The filename to parse.

    Returns:
        A dict with ``round``, ``role``, ``timestamp`` keys, or None if invalid.
    """
    pattern = r"^trajectory_(\d+)_(plan_gen|code_gen|reflect)_(\d{8}T\d{6})\.json$"
    match = re.match(pattern, filename)
    if not match:
        return None
    return {
        "round": int(match.group(1)),
        "role": match.group(2),
        "timestamp": match.group(3),
    }
