"""Tests for src/main.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config import (
    AgentConfig,
    Config,
    DockerConfig,
    EvaluatorConfig,
    PromptConfig,
    SystemConfig,
)
from src.exceptions import FatalError
from src.main import _override_config, main, parse_args


@pytest.fixture
def config() -> Config:
    return Config(
        system=SystemConfig(
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            n=3,
            swe_pro_instances=["astropy__astropy-14539"],
            output_dir="./output",
        ),
        prompts=PromptConfig(),
        docker=DockerConfig(),
        agent=AgentConfig(max_steps=10),
        deepseek_api_key="test-key",
    )


class TestParseArgs:
    def test_default_values(self):
        args = parse_args([])
        assert args.config == "config.yaml"
        assert args.instance is None
        assert args.n is None
        assert args.output_dir is None
        assert args.verbose is False

    def test_instance_override(self):
        args = parse_args(["--instance", "django__django-123"])
        assert args.instance == "django__django-123"

    def test_n_override(self):
        args = parse_args(["--n", "5"])
        assert args.n == 5

    def test_output_dir_override(self):
        args = parse_args(["--output-dir", "./results"])
        assert args.output_dir == "./results"

    def test_verbose_flag(self):
        args = parse_args(["-v"])
        assert args.verbose is True


class TestOverrideConfig:
    def test_n_override(self, config):
        args = parse_args(["--n", "5"])
        new_config = _override_config(config, args)
        assert new_config.system.n == 5

    def test_output_dir_override(self, config):
        args = parse_args(["--output-dir", "./results"])
        new_config = _override_config(config, args)
        assert new_config.system.output_dir == "./results"

    def test_no_override(self, config):
        args = parse_args([])
        new_config = _override_config(config, args)
        assert new_config.system.n == 3
        assert new_config.system.output_dir == "./output"

    def test_override_preserves_evaluator(self):
        """Regression: _override_config used to drop evaluator when rebuilding
        the frozen Config, silently resetting it to the default timeout."""
        cfg = Config(
            system=SystemConfig(
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
                n=3,
                swe_pro_instances=["i1"],
                output_dir="./output",
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            evaluator=EvaluatorConfig(timeout=999),
            deepseek_api_key="test-key",
        )
        args = parse_args(["--n", "5"])
        new_cfg = _override_config(cfg, args)
        assert new_cfg.system.n == 5
        # Critical: user-set evaluator.timeout must survive the rebuild
        assert new_cfg.evaluator.timeout == 999


class TestMain:
    @patch("src.main.load_config")
    @patch("src.main.run_instance")
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_runs_single_instance(self, mock_run, mock_load, config):
        mock_load.return_value = config
        mock_run.return_value = {"plans": []}

        exit_code = main(["--instance", "django__django-123", "--n", "1"])
        assert exit_code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == "django__django-123"
        assert call_args[0][1].system.n == 1

    @patch("src.main.load_config")
    @patch("src.main.run_instance")
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_runs_multiple_instances(self, mock_run, mock_load, config):
        mock_load.return_value = config
        mock_run.return_value = {"plans": []}

        exit_code = main([])
        assert exit_code == 0
        assert mock_run.call_count == 1  # config has 1 instance

    @patch("src.main.load_config")
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_fatal_error_returns_nonzero(self, mock_load, config):
        mock_load.return_value = config
        with patch("src.main.run_instance", side_effect=FatalError("API failed")):
            exit_code = main(["--instance", "test__test-1"])
        assert exit_code == 1

    @patch("src.main.load_config")
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_load_config_error_returns_nonzero(self, mock_load):
        mock_load.side_effect = FatalError("Config not found")
        exit_code = main(["--config", "missing.yaml"])
        assert exit_code == 1

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_api_key_returns_nonzero(self):
        exit_code = main(["--instance", "test__test-1"])
        assert exit_code == 1

    @patch("src.main.load_config")
    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"})
    def test_no_instances_returns_nonzero(self, mock_load, config):
        config_no_instances = Config(
            system=SystemConfig(
                model="deepseek-v4-flash",
                n=1,
                swe_pro_instances=[],
                output_dir="./output",
            ),
            prompts=PromptConfig(),
            docker=DockerConfig(),
            agent=AgentConfig(max_steps=10),
            deepseek_api_key="test-key",
        )
        mock_load.return_value = config_no_instances
        exit_code = main([])
        assert exit_code == 1
