"""Small JSON/JSONL file helpers shared by internal scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write JSON object records as newline-delimited JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records
    )
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a JSON object."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read newline-delimited JSON objects."""
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(record)
    return records
