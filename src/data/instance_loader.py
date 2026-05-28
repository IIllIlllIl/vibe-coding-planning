"""SWE-bench instance metadata loader.

Supports loading from the swebench package or from local mock JSON files.
The dataset (Verified vs Pro) is selected via the ``dataset`` constructor
argument and passed through to ``load_swebench_dataset(name=...)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.exceptions import TaskError

logger = logging.getLogger(__name__)


class InstanceLoader:
    """Loads SWE-bench instance metadata."""

    def __init__(
        self,
        mock_data_dir: str | Path | None = None,
        *,
        dataset: str | None = None,
    ) -> None:
        """Initialize the loader.

        Args:
            mock_data_dir: Directory containing mock instance JSON files.
                Each file should be named ``{instance_id}.json``.
            dataset: Hugging Face dataset name forwarded to
                ``load_swebench_dataset(name=...)``. ``None`` (default)
                uses swebench's own default (``SWE-bench/SWE-bench``).
                Pipeline callers should pass ``config.system.dataset``
                explicitly so Verified vs Pro is unambiguous.
        """
        self.mock_data_dir = Path(mock_data_dir) if mock_data_dir else None
        self.dataset = dataset

    def load_instance(self, instance_id: str) -> dict[str, Any]:
        """Load metadata for a single SWE-bench instance.

        If ``mock_data_dir`` is set, loads from a local JSON file named
        ``{instance_id}.json``. Otherwise, attempts to load via swebench
        from the dataset configured in ``__init__`` (Verified by default
        when called from the pipeline).

        Args:
            instance_id: The SWE-bench instance identifier.

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

        Calls ``swebench.harness.utils.load_swebench_dataset`` to fetch
        instance metadata from the HuggingFace SWE-bench dataset.

        Args:
            instance_id: The SWE-bench instance identifier.

        Returns:
            Instance metadata dict compatible with the pipeline and evaluator.

        Raises:
            TaskError: If the instance cannot be found or swebench is not
                installed.
        """
        try:
            from swebench.harness.utils import load_swebench_dataset
        except ImportError as exc:
            raise TaskError(
                "swebench is not installed. "
                "Please install it: pip install swebench>=4.1.0"
            ) from exc

        kwargs: dict[str, Any] = {"instance_ids": [instance_id]}
        if self.dataset is not None:
            kwargs["name"] = self.dataset

        try:
            instances = load_swebench_dataset(**kwargs)
        except Exception as exc:
            raise TaskError(
                f"Failed to load instance {instance_id} from SWE-bench dataset "
                f"({self.dataset or 'default'}): {exc}"
            ) from exc

        if not instances:
            raise TaskError(
                f"Instance {instance_id} not found in SWE-bench dataset "
                f"({self.dataset or 'default'})."
            )

        instance = dict(instances[0])

        # Ensure problem_statement is present (fallbacks for compatibility)
        if "problem_statement" not in instance and "text" in instance:
            instance["problem_statement"] = instance["text"]

        # SWE-bench Pro instances use pre-built images on Docker Hub
        dockerhub_tag = instance.get("dockerhub_tag")
        if dockerhub_tag:
            instance["image_name"] = f"jefzda/sweap-images:{dockerhub_tag}"
            logger.info(
                "SWE-bench Pro image mapped: %s -> %s",
                instance_id,
                instance["image_name"],
            )

        logger.info(
            "Loaded SWE-bench instance: %s (repo=%s, dataset=%s)",
            instance_id,
            instance.get("repo"),
            self.dataset or "default",
        )
        return instance

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
