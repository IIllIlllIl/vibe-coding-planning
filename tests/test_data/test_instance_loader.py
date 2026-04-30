"""Tests for src/data/instance_loader.py."""

import json
from pathlib import Path

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
    def test_raises_without_mock_dir(self):
        loader = InstanceLoader()
        with pytest.raises(TaskError, match="not yet implemented"):
            loader.load_instance("astropy__astropy-14539")

    def test_list_empty_without_mock_dir(self):
        loader = InstanceLoader()
        assert loader.list_available_instances() == []
