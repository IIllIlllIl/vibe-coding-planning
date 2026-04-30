"""Tests for src/config.py."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import (
    AgentConfig,
    Config,
    DockerConfig,
    PromptConfig,
    ResumeConfig,
    SystemConfig,
    load_config,
)
from src.exceptions import FatalError


@pytest.fixture
def valid_config_dict() -> dict[str, Any]:
    return {
        "system": {
            "n": 3,
            "optimization_info_level": 1,
            "model": "deepseek-v4-flash",
            "swe_pro_instances": ["astropy__astropy-14539"],
            "output_dir": "./output",
            "use_gepa_reflection_prompt": True,
            "resume": {
                "enabled": False,
                "from_plan_id": "",
                "from_round": 0,
            },
        },
        "prompts": {
            "plan_generation_prompt": "Plan prompt here",
            "code_generation_prompt": "Code prompt here",
            "plan_optimization_prompt": "Optimize prompt here",
            "plan_format_template": "Format template here",
        },
        "docker": {
            "image_builder_script": "./scripts/build.sh",
            "workdir": "/testbed",
            "codebase_mount_options": "ro",
            "timeout": 30,
        },
        "agent": {
            "max_steps": 30,
            "cost_limit": 3.0,
        },
    }


@pytest.fixture
def config_file(tmp_path: Path, valid_config_dict: dict[str, Any]) -> Path:
    filepath = tmp_path / "config.yaml"
    filepath.write_text(yaml.dump(valid_config_dict), encoding="utf-8")
    return filepath


class TestLoadConfigSuccess:
    def test_loads_all_fields(self, monkeypatch, config_file: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-123")
        config = load_config(config_file)

        assert isinstance(config, Config)
        assert config.system.n == 3
        assert config.system.optimization_info_level == 1
        assert config.system.model == "deepseek-v4-flash"
        assert config.system.swe_pro_instances == ["astropy__astropy-14539"]
        assert config.system.output_dir == "./output"
        assert config.system.use_gepa_reflection_prompt is True
        assert config.prompts.plan_generation_prompt == "Plan prompt here"
        assert config.prompts.code_generation_prompt == "Code prompt here"
        assert config.prompts.plan_optimization_prompt == "Optimize prompt here"
        assert config.prompts.plan_format_template == "Format template here"
        assert config.docker.image_builder_script == "./scripts/build.sh"
        assert config.docker.workdir == "/testbed"
        assert config.docker.codebase_mount_options == "ro"
        assert config.docker.timeout == 30
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
        assert config.deepseek_api_key == "test-key-123"

    def test_api_key_injected(self, monkeypatch, config_file: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "my-secret-key")
        config = load_config(config_file)
        assert config.deepseek_api_key == "my-secret-key"


class TestLoadConfigValidation:
    def test_n_less_than_one_raises_fatal_error(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"n": 0}}
        filepath = tmp_path / "bad_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(FatalError, match="n.*must be >= 1"):
            load_config(filepath)

    def test_negative_n_raises_fatal_error(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"n": -1}}
        filepath = tmp_path / "bad_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")

        with pytest.raises(FatalError, match="n.*must be >= 1"):
            load_config(filepath)

    def test_missing_api_key_raises_fatal_error(self, monkeypatch, config_file: Path):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(FatalError, match="DEEPSEEK_API_KEY"):
            load_config(config_file)

    def test_optimization_info_level_invalid_defaults_to_zero(
        self, monkeypatch, tmp_path: Path, caplog
    ):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"optimization_info_level": 5}}
        filepath = tmp_path / "warn_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)

        assert config.system.optimization_info_level == 0
        assert "optimization_info_level" in caplog.text

    def test_missing_config_file_raises_fatal_error(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        missing = tmp_path / "nonexistent.yaml"
        with pytest.raises(FatalError, match="not found"):
            load_config(missing)


class TestResumeConfig:
    def test_resume_enabled_with_empty_plan_id_warns(
        self, monkeypatch, tmp_path: Path, caplog
    ):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {
            "system": {
                "resume": {
                    "enabled": True,
                    "from_plan_id": "",
                    "from_round": 1,
                }
            }
        }
        filepath = tmp_path / "resume_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)

        assert config.system.resume.enabled is True
        assert "from_plan_id is empty" in caplog.text

    def test_resume_enabled_with_low_round_warns(
        self, monkeypatch, tmp_path: Path, caplog
    ):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {
            "system": {
                "resume": {
                    "enabled": True,
                    "from_plan_id": "plan_001",
                    "from_round": 0,
                }
            }
        }
        filepath = tmp_path / "resume_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)

        assert config.system.resume.enabled is True
        assert "from_round < 1" in caplog.text


class TestBooleanParsing:
    def test_use_gepa_string_true(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"use_gepa_reflection_prompt": "true"}}
        filepath = tmp_path / "bool_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.system.use_gepa_reflection_prompt is True

    def test_use_gepa_string_false(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"use_gepa_reflection_prompt": "false"}}
        filepath = tmp_path / "bool_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.system.use_gepa_reflection_prompt is False

    def test_use_gepa_int_one(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"use_gepa_reflection_prompt": 1}}
        filepath = tmp_path / "bool_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.system.use_gepa_reflection_prompt is True


class TestDefaults:
    def test_empty_config_uses_defaults(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty_config.yaml"
        filepath.write_text("{}", encoding="utf-8")
        config = load_config(filepath)

        assert config.system.n == 3
        assert config.system.model == "deepseek-v4-flash"
        assert config.system.optimization_info_level == 1
        assert config.system.use_gepa_reflection_prompt is True
        assert config.system.swe_pro_instances == []
        assert config.docker.workdir == "/testbed"
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
