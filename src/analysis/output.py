"""Output handling for contrastive analysis results.

Writes per-case results, trajectories, and aggregates into JSON/JSONL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AnalysisOutputWriter:
    """Handles all file output for the contrastive analysis run."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.per_case_dir = self.output_dir / "per_case"
        self.trajectories_dir = self.output_dir / "trajectories"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.per_case_dir.mkdir(parents=True, exist_ok=True)
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)

    def save_result(
        self,
        *,
        instance_id: str,
        rule: str,
        rule_valid: bool,
        steps_used: int | None = None,
        cost: float | None = None,
        error: str | None = None,
    ) -> Path:
        """Save a single case's analysis result."""
        result: dict[str, Any] = {
            "instance_id": instance_id,
            "rule": rule,
            "rule_valid": rule_valid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if steps_used is not None:
            result["steps_used"] = steps_used
        if cost is not None:
            result["cost"] = cost
        if error is not None:
            result["error"] = error

        path = self.per_case_dir / f"{instance_id}.json"
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Result saved to %s", path)
        return path

    def save_trajectory(
        self,
        instance_id: str,
        messages: list[dict[str, Any]],
    ) -> Path:
        """Save the agent's message trajectory for a case."""
        data = {
            "instance_id": instance_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "messages": messages,
        }
        path = self.trajectories_dir / f"{instance_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Trajectory saved to %s", path)
        return path

    def append_rule_jsonl(self, result: dict[str, Any]) -> None:
        """Append a result dict to the aggregate rules.jsonl file."""
        rules_path = self.output_dir / "rules.jsonl"
        with rules_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    def append_error_jsonl(self, error_record: dict[str, Any]) -> None:
        """Append an error record to the aggregate errors.jsonl file."""
        errors_path = self.output_dir / "errors.jsonl"
        with errors_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(error_record, ensure_ascii=False) + "\n")
