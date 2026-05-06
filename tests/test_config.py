"""Tests for src/config.py."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.config import (
    Config,
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
            "api_base": "https://api.deepseek.com",
            "swe_pro_instances": ["astropy__astropy-14539"],
            "output_dir": "./output",
            "resume": {
                "enabled": False,
                "from_plan_id": "",
                "from_round": 0,
            },
        },
        "prompts": {
            "plan_generation_prompt": "Plan prompt here",
            "code_generation_prompt": "Code prompt here",
            "reflection_prompt_template": "Reflect template {prompt_template} {inputs_outputs_feedback}",
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
            "timeout": 120,
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
        assert config.system.api_base == "https://api.deepseek.com"
        assert config.system.swe_pro_instances == ["astropy__astropy-14539"]
        assert config.system.output_dir == "./output"
        assert config.prompts.plan_generation_prompt == "Plan prompt here"
        assert config.prompts.code_generation_prompt == "Code prompt here"
        assert (
            config.prompts.reflection_prompt_template
            == "Reflect template {prompt_template} {inputs_outputs_feedback}"
        )
        assert config.prompts.plan_format_template == "Format template here"
        assert config.docker.image_builder_script == "./scripts/build.sh"
        assert config.docker.workdir == "/testbed"
        assert config.docker.codebase_mount_options == "ro"
        assert config.docker.timeout == 30
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
        assert config.agent.timeout == 120
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


class TestReflectionTemplateFallback:
    """Verify PromptConfig.reflection_prompt_template falls back to
    DEFAULT_REFLECTION_TEMPLATE when the user omits or empties the field."""

    def test_missing_falls_back_to_default(self, monkeypatch, tmp_path: Path):
        from src.prompts.gepa_reflection import DEFAULT_REFLECTION_TEMPLATE

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        # Empty config — no prompts section at all
        filepath = tmp_path / "no_prompts.yaml"
        filepath.write_text("{}", encoding="utf-8")
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == DEFAULT_REFLECTION_TEMPLATE

    def test_empty_string_falls_back_to_default(self, monkeypatch, tmp_path: Path):
        from src.prompts.gepa_reflection import DEFAULT_REFLECTION_TEMPLATE

        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"prompts": {"reflection_prompt_template": ""}}
        filepath = tmp_path / "empty_tpl.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == DEFAULT_REFLECTION_TEMPLATE

    def test_user_template_overrides_default(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        custom = "MY-CUSTOM {prompt_template} {inputs_outputs_feedback}"
        data = {"prompts": {"reflection_prompt_template": custom}}
        filepath = tmp_path / "custom_tpl.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == custom


class TestApiBaseAndTimeout:
    def test_custom_api_base(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "https://custom.example.com"}}
        filepath = tmp_path / "api_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.system.api_base == "https://custom.example.com"

    def test_custom_agent_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"timeout": 60}}
        filepath = tmp_path / "timeout_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.agent.timeout == 60


class TestAgentValidation:
    def test_max_steps_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"max_steps": 0}}
        filepath = tmp_path / "bad_agent.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="max_steps.*must be >= 1"):
            load_config(filepath)

    def test_cost_limit_negative_defaults_to_zero(self, monkeypatch, tmp_path: Path, caplog):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"cost_limit": -1.0}}
        filepath = tmp_path / "warn_agent.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)
        assert config.agent.cost_limit == 0.0

    def test_timeout_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"timeout": 0}}
        filepath = tmp_path / "bad_agent.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="timeout.*must be >= 1"):
            load_config(filepath)


class TestDockerValidation:
    def test_docker_timeout_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"docker": {"timeout": 0}}
        filepath = tmp_path / "bad_docker.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="timeout.*must be >= 1"):
            load_config(filepath)


class TestApiBaseValidation:
    def test_invalid_api_base_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "not-a-url"}}
        filepath = tmp_path / "bad_api.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="Invalid api_base URL"):
            load_config(filepath)

    def test_missing_scheme_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "deepseek.com"}}
        filepath = tmp_path / "bad_api.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="Invalid api_base URL"):
            load_config(filepath)


class TestEvaluatorConfig:
    def test_default_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        filepath.write_text("{}", encoding="utf-8")
        config = load_config(filepath)
        assert config.evaluator.timeout == 1800

    def test_custom_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": 600}}
        filepath = tmp_path / "eval_config.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.evaluator.timeout == 600

    def test_zero_timeout_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": 0}}
        filepath = tmp_path / "bad_eval.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="evaluator.timeout.*must be >= 1"):
            load_config(filepath)

    def test_negative_timeout_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": -10}}
        filepath = tmp_path / "bad_eval.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="evaluator.timeout.*must be >= 1"):
            load_config(filepath)


class TestDefaults:
    def test_empty_config_uses_defaults(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty_config.yaml"
        filepath.write_text("{}", encoding="utf-8")
        config = load_config(filepath)

        assert config.system.n == 3
        assert config.system.model == "deepseek-v4-flash"
        assert config.system.api_base == "https://api.deepseek.com"
        assert config.system.optimization_info_level == 1
        assert config.system.swe_pro_instances == []
        assert config.docker.workdir == "/testbed"
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
        assert config.agent.timeout == 120
