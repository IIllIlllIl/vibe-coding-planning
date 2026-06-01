"""SWE-bench instance metadata loader.

Supports loading from the swebench package, from HuggingFace PolyBench datasets,
or from local mock JSON files. The dataset (Verified vs Pro vs PolyBench) is
selected via the ``dataset`` constructor argument and passed through to the
appropriate loader.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from src.exceptions import TaskError

logger = logging.getLogger(__name__)


class InstanceLoader:
    """Loads SWE-bench or PolyBench instance metadata."""

    def __init__(
        self,
        mock_data_dir: str | Path | None = None,
        *,
        dataset: str | None = None,
        dataset_type: str | None = None,
        language_filter: str | None = None,
    ) -> None:
        """Initialize the loader.

        Args:
            mock_data_dir: Directory containing mock instance JSON files.
                Each file should be named ``{instance_id}.json``.
            dataset: Hugging Face dataset name forwarded to the loader.
                ``None`` (default) uses swebench's own default.
            dataset_type: Explicit dataset type hint. ``"polybench"`` forces
                PolyBench loading mode. When empty, the type is inferred from
                the ``dataset`` name.
            language_filter: For PolyBench multi-language datasets, only
                return instances where ``language`` matches this value
                (e.g. ``"Python"``).
        """
        self.mock_data_dir = Path(mock_data_dir) if mock_data_dir else None
        self.dataset = dataset
        self.dataset_type = dataset_type or ""
        self.language_filter = language_filter or ""
        self._polybench_cache: dict[str, dict[str, Any]] | None = None

    def _is_polybench_dataset(self) -> bool:
        """Return True if the configured dataset is a PolyBench dataset."""
        if self.dataset_type and self.dataset_type.lower() == "polybench":
            return True
        if self.dataset and "polybench" in self.dataset.lower():
            return True
        return False

    def load_instance(self, instance_id: str) -> dict[str, Any]:
        """Load metadata for a single instance.

        If ``mock_data_dir`` is set, loads from a local JSON file named
        ``{instance_id}.json``. Otherwise, attempts to load via swebench
        or PolyBench depending on the configured dataset.

        Args:
            instance_id: The instance identifier.

        Returns:
            A dict containing instance metadata.

        Raises:
            TaskError: If the instance cannot be found or loaded.
        """
        if self.mock_data_dir is not None:
            return self._load_from_mock(instance_id)

        if self._is_polybench_dataset():
            return self._load_from_polybench(instance_id)

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

    def _load_polybench_cache(self) -> dict[str, dict[str, Any]]:
        """Lazy-load the full PolyBench dataset into an in-memory cache."""
        if self._polybench_cache is not None:
            return self._polybench_cache

        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise TaskError(
                "datasets is not installed. "
                "Please install it: pip install datasets"
            ) from exc

        dataset_name = self.dataset or "AmazonScience/SWE-PolyBench"
        logger.info(
            "Loading PolyBench dataset: %s (language_filter=%s)",
            dataset_name,
            self.language_filter or "None",
        )

        try:
            ds = load_dataset(dataset_name, split="test")
        except Exception as exc:
            raise TaskError(
                f"Failed to load PolyBench dataset {dataset_name}: {exc}"
            ) from exc

        try:
            df = ds.to_pandas()
        except Exception as exc:
            raise TaskError(
                f"Failed to convert PolyBench dataset to pandas: {exc}"
            ) from exc

        if self.language_filter:
            df = df[df["language"] == self.language_filter]

        cache: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            inst = dict(row)
            inst = self._normalize_polybench_fields(inst)
            inst_id = inst.get("instance_id", "")
            if inst_id:
                cache[inst_id] = inst

        self._polybench_cache = cache
        logger.info(
            "PolyBench cache loaded: %d instances (%s)",
            len(cache),
            dataset_name,
        )
        return cache

    @staticmethod
    def _normalize_polybench_fields(instance: dict[str, Any]) -> dict[str, Any]:
        """Normalize PolyBench field names to match swebench conventions.

        PolyBench uses CamelCase for some fields and stores list-valued
        columns as JSON strings. This method maps:

        - ``Dockerfile`` → ``dockerfile``
        - ``F2P`` / ``P2P`` / ``F2F`` → ``f2p`` / ``p2p`` / ``f2f`` (parsed from string)
        - ``modified_nodes`` → parsed from JSON string to list
        """
        # Dockerfile casing
        if "Dockerfile" in instance:
            instance["dockerfile"] = instance.pop("Dockerfile")

        # Parse JSON-string list fields
        for old_key, new_key in [
            ("F2P", "f2p"),
            ("P2P", "p2p"),
            ("F2F", "f2f"),
        ]:
            if old_key in instance:
                raw = instance.pop(old_key)
                if isinstance(raw, str):
                    try:
                        raw = ast.literal_eval(raw)
                    except Exception:
                        raw = []
                instance[new_key] = raw

        # modified_nodes may be a JSON string
        if isinstance(instance.get("modified_nodes"), str):
            try:
                instance["modified_nodes"] = ast.literal_eval(
                    instance["modified_nodes"]
                )
            except Exception:
                instance["modified_nodes"] = []

        return instance

    def _load_from_polybench(self, instance_id: str) -> dict[str, Any]:
        """Load a PolyBench instance from the cached dataset."""
        cache = self._load_polybench_cache()
        instance = cache.get(instance_id)
        if instance is None:
            raise TaskError(
                f"Instance {instance_id} not found in PolyBench dataset "
                f"({self.dataset or 'default'}). "
                f"Total cached instances: {len(cache)}."
            )

        # Derive image name for PolyBench instances
        instance["image_name"] = (
            f"ghcr.io/timesler/swe-polybench.eval.x86_64."
            f"{instance_id.lower()}:v1.1"
        )
        instance["dataset_type"] = "polybench"
        # PolyBenchInstance requires model_patch (set empty; actual patch
        # is supplied separately at evaluation time).
        instance.setdefault("model_patch", "")

        # Ensure problem_statement is present
        if "problem_statement" not in instance and "text" in instance:
            instance["problem_statement"] = instance["text"]

        logger.info(
            "Loaded PolyBench instance: %s (repo=%s, language=%s)",
            instance_id,
            instance.get("repo"),
            instance.get("language"),
        )
        return instance

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
        If using PolyBench mode, returns all cached instance IDs.
        Otherwise returns an empty list (swebench mode requires explicit IDs).

        Returns:
            List of available instance IDs.
        """
        if self.mock_data_dir is not None:
            instances = []
            for filepath in self.mock_data_dir.glob("*.json"):
                instances.append(filepath.stem)
            return sorted(instances)

        if self._is_polybench_dataset():
            cache = self._load_polybench_cache()
            return sorted(cache.keys())

        return []
