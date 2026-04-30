"""SWE-bench Pro instance metadata loader.

Supports loading from the swebench package or from local mock JSON files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.exceptions import TaskError

logger = logging.getLogger(__name__)


class InstanceLoader:
    """Loads SWE-bench Pro instance metadata."""

    def __init__(self, mock_data_dir: str | Path | None = None) -> None:
        """Initialize the loader.

        Args:
            mock_data_dir: Directory containing mock instance JSON files.
                Each file should be named ``{instance_id}.json``.
        """
        self.mock_data_dir = Path(mock_data_dir) if mock_data_dir else None

    def load_instance(self, instance_id: str) -> dict[str, Any]:
        """Load metadata for a single SWE-bench Pro instance.

        If ``mock_data_dir`` is set, loads from a local JSON file named
        ``{instance_id}.json``. Otherwise, attempts to load via swebench.

        Args:
            instance_id: The SWE-bench Pro instance identifier.

        Returns:
            A dict containing instance metadata (instance_id, repo, base_commit,
            test_patch, patch, requirements_txt, etc.).

        Raises:
            TaskError: If the instance cannot be found or loaded.
        """
        if self.mock_data_dir is not None:
            return self._load_from_mock(instance_id)

        return self._load_from_swebench(instance_id)

    def _load_from_mock(self, instance_id: str) -> dict[str, Any]:
        """Load instance from a local mock JSON file."""
        if self.mock_data_dir is None:
            raise TaskError("mock_data_dir is not set")

        filepath = self.mock_data_dir / f"{instance_id}.json"
        if not filepath.exists():
            raise TaskError(
                f"Mock instance file not found: {filepath}"
            )

        try:
            with filepath.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise TaskError(
                f"Invalid JSON in mock instance file {filepath}: {exc}"
            ) from exc

        # Validate required fields
        required = {"instance_id", "repo", "base_commit"}
        missing = required - set(data.keys())
        if missing:
            raise TaskError(
                f"Mock instance {instance_id} missing required fields: {missing}"
            )

        return data

    def _load_from_swebench(self, instance_id: str) -> dict[str, Any]:
        """Load instance via the swebench package.

        This is a placeholder for the real swebench integration. In the full
        implementation, this will call swebench API functions to load instance
        metadata from the SWE-bench Pro dataset.
        """
        # TODO: Implement real swebench integration in a later round.
        # For now, raise TaskError to indicate this requires real data.
        raise TaskError(
            f"Real swebench loading not yet implemented for {instance_id}. "
            "Use mock_data_dir for testing."
        )

    def list_available_instances(self) -> list[str]:
        """List available instance IDs.

        If using mock mode, lists JSON files in the mock directory.
        Otherwise returns an empty list (swebench mode requires explicit IDs).

        Returns:
            List of available instance IDs.
        """
        if self.mock_data_dir is None:
            return []

        instances = []
        for filepath in self.mock_data_dir.glob("*.json"):
            instances.append(filepath.stem)
        return sorted(instances)
