"""Tests for src/data/instance_loader.py."""

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from src.data.instance_loader import InstanceLoader
from src.exceptions import TaskError


@pytest.fixture
def mock_instance_data() -> dict:
    return {
        "instance_id": "astropy__astropy-14539",
        "repo": "astropy/astropy",
        "base_commit": "abc123",
        "test_patch": "test patch content",
        "patch": "patch content",
        "requirements_txt": "numpy\npytest",
    }


@pytest.fixture
def mock_data_dir(tmp_path: Path, mock_instance_data: dict) -> Path:
    data_dir = tmp_path / "mock_instances"
    data_dir.mkdir()
    filepath = data_dir / "astropy__astropy-14539.json"
    filepath.write_text(json.dumps(mock_instance_data), encoding="utf-8")
    return data_dir


class TestMockMode:
    def test_load_valid_instance(self, mock_data_dir: Path, mock_instance_data: dict):
        loader = InstanceLoader(mock_data_dir)
        result = loader.load_instance("astropy__astropy-14539")
        assert result == mock_instance_data

    def test_missing_instance_raises(self, mock_data_dir: Path):
        loader = InstanceLoader(mock_data_dir)
        with pytest.raises(TaskError, match="not found"):
            loader.load_instance("nonexistent__repo-99999")

    def test_invalid_json_raises(self, mock_data_dir: Path):
        bad_file = mock_data_dir / "bad__instance-1.json"
        bad_file.write_text("not valid json", encoding="utf-8")
        loader = InstanceLoader(mock_data_dir)
        with pytest.raises(TaskError, match="Invalid JSON"):
            loader.load_instance("bad__instance-1")

    def test_missing_required_fields_raises(self, mock_data_dir: Path):
        incomplete = {"instance_id": "incomplete__repo-1"}
        filepath = mock_data_dir / "incomplete__repo-1.json"
        filepath.write_text(json.dumps(incomplete), encoding="utf-8")
        loader = InstanceLoader(mock_data_dir)
        with pytest.raises(TaskError, match="missing required fields"):
            loader.load_instance("incomplete__repo-1")

    def test_list_available_instances(self, mock_data_dir: Path):
        loader = InstanceLoader(mock_data_dir)
        instances = loader.list_available_instances()
        assert "astropy__astropy-14539" in instances


class TestSwebenchMode:
    @patch("swebench.harness.utils.load_swebench_dataset")
    def test_raises_when_instance_not_found(self, mock_load_dataset):
        mock_load_dataset.return_value = []
        loader = InstanceLoader()
        with pytest.raises(TaskError, match="not found"):
            loader.load_instance("astropy__astropy-14539")

    @patch("swebench.harness.utils.load_swebench_dataset")
    def test_loads_real_instance(self, mock_load_dataset, mock_instance_data):
        mock_load_dataset.return_value = [mock_instance_data]
        loader = InstanceLoader()
        result = loader.load_instance("astropy__astropy-14539")
        assert result["instance_id"] == "astropy__astropy-14539"
        assert result["repo"] == "astropy/astropy"

    def test_list_empty_without_mock_dir(self):
        loader = InstanceLoader()
        assert loader.list_available_instances() == []


class TestPolybenchMode:
    """Tests for PolyBench dataset loading via the datasets library."""

    @patch("datasets.load_dataset")
    def test_load_polybench_instance(self, mock_load_dataset):
        """Loading a PolyBench Python instance returns normalized fields."""
        mock_row = {
            "instance_id": "huggingface__transformers-3147",
            "repo": "huggingface/transformers",
            "base_commit": "abc123",
            "patch": "diff content",
            "test_patch": "test diff content",
            "problem_statement": "Fix bug in transformers",
            "language": "Python",
            "Dockerfile": "FROM python:3.10",
            "F2P": "['test_f2p']",
            "P2P": "['test_p2p']",
            "F2F": "[]",
            "test_command": "pytest tests/",
            "modified_nodes": '["file.py->func->foo"]',
        }
        mock_ds = type("MockDataset", (), {"to_pandas": lambda self: pd.DataFrame([mock_row])})()
        mock_load_dataset.return_value = mock_ds

        loader = InstanceLoader(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            language_filter="Python",
        )
        result = loader.load_instance("huggingface__transformers-3147")

        assert result["instance_id"] == "huggingface__transformers-3147"
        assert result["repo"] == "huggingface/transformers"
        assert result["language"] == "Python"
        # Field name normalization
        assert "dockerfile" in result
        assert "Dockerfile" not in result
        assert result["dockerfile"] == "FROM python:3.10"
        # JSON-string list fields parsed
        assert result["f2p"] == ["test_f2p"]
        assert result["p2p"] == ["test_p2p"]
        assert result["f2f"] == []
        assert result["modified_nodes"] == ["file.py->func->foo"]
        # Image name derived
        assert "ghcr.io/timesler/swe-polybench.eval.x86_64." in result["image_name"]
        assert result["dataset_type"] == "polybench"
        assert result["model_patch"] == ""

    @patch("datasets.load_dataset")
    def test_list_available_instances_polybench(self, mock_load_dataset):
        """list_available_instances returns all cached PolyBench IDs."""
        rows = [
            {"instance_id": "repo__repo-1", "language": "Python"},
            {"instance_id": "repo__repo-2", "language": "Python"},
            {"instance_id": "repo__repo-3", "language": "Java"},
        ]
        mock_ds = type("MockDataset", (), {"to_pandas": lambda self: pd.DataFrame(rows)})()
        mock_load_dataset.return_value = mock_ds

        loader = InstanceLoader(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            language_filter="Python",
        )
        instances = loader.list_available_instances()
        assert instances == ["repo__repo-1", "repo__repo-2"]

    @patch("datasets.load_dataset")
    def test_missing_instance_raises(self, mock_load_dataset):
        mock_ds = type("MockDataset", (), {"to_pandas": lambda self: pd.DataFrame([])})()
        mock_load_dataset.return_value = mock_ds

        loader = InstanceLoader(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
        )
        with pytest.raises(TaskError, match="not found"):
            loader.load_instance("nonexistent__repo-99999")

    @patch("datasets.load_dataset")
    def test_infer_polybench_from_dataset_name(self, mock_load_dataset):
        """When dataset_type is empty but dataset name contains 'polybench',
        the loader automatically enters PolyBench mode."""
        mock_ds = type("MockDataset", (), {"to_pandas": lambda self: pd.DataFrame([])})()
        mock_load_dataset.return_value = mock_ds

        loader = InstanceLoader(
            dataset="AmazonScience/SWE-PolyBench",
        )
        assert loader._is_polybench_dataset() is True

    def test_infer_swebench_when_no_hint(self):
        loader = InstanceLoader(dataset="SWE-bench/SWE-bench_Verified")
        assert loader._is_polybench_dataset() is False

    @patch("datasets.load_dataset")
    def test_language_filter_applied(self, mock_load_dataset):
        """Only instances matching language_filter are loaded."""
        rows = [
            {"instance_id": "py-1", "language": "Python"},
            {"instance_id": "java-1", "language": "Java"},
        ]
        mock_ds = type("MockDataset", (), {"to_pandas": lambda self: pd.DataFrame(rows)})()
        mock_load_dataset.return_value = mock_ds

        loader = InstanceLoader(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            language_filter="Python",
        )
        instances = loader.list_available_instances()
        assert instances == ["py-1"]

    def test_normalize_polybench_fields(self):
        """Static method correctly normalizes CamelCase and JSON-string fields."""
        raw = {
            "instance_id": "test-1",
            "Dockerfile": "FROM python:3.10",
            "F2P": "['a', 'b']",
            "P2P": "[]",
            "F2F": "['c']",
            "modified_nodes": '["n1", "n2"]',
        }
        normalized = InstanceLoader._normalize_polybench_fields(raw)
        assert normalized["dockerfile"] == "FROM python:3.10"
        assert "Dockerfile" not in normalized
        assert normalized["f2p"] == ["a", "b"]
        assert normalized["p2p"] == []
        assert normalized["f2f"] == ["c"]
        assert normalized["modified_nodes"] == ["n1", "n2"]
