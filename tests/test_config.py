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


# ---------------------------------------------------------------------------
# Helper: write a YAML test config with a default ``system.batch_id``.
#
# The loader requires ``system.batch_id`` to be non-empty (FatalError
# otherwise). Most tests focus on other validation paths and don't care
# what the batch_id is — they go through this helper so they inherit a
# valid default. Tests that intentionally exercise batch_id validation
# (TestBatchIdValidation below) write YAML directly to opt out.
# ---------------------------------------------------------------------------
def _write_test_config(filepath: Path, data: dict[str, Any] | None = None) -> None:
    """Write a YAML test config with a default ``system.batch_id`` injected."""
    payload = dict(data or {})
    sys_block = dict(payload.get("system", {}))
    sys_block.setdefault("batch_id", "test_batch")
    payload["system"] = sys_block
    filepath.write_text(yaml.dump(payload), encoding="utf-8")


@pytest.fixture
def valid_config_dict() -> dict[str, Any]:
    return {
        "system": {
            "n": 3,
            "optimization_info_level": 1,
            "model": "deepseek-v4-flash",
            "api_base": "https://api.deepseek.com",
            "dataset": "SWE-bench/SWE-bench_Verified",
            "instances": ["astropy__astropy-12907"],
            "output_dir": "./output",
            "batch_id": "test_batch",
            "resume": {
                "enabled": False,
                "from_plan_id": "",
                "from_round": 0,
            },
        },
        "prompts": {
            "plan_generation_prompt": "Plan prompt here",
            "plan_instance_template": "<pr_description>{{task}}</pr_description>",
            "code_generation_prompt": "Code prompt here",
            "code_instance_template": "<pr_description>{{task}}</pr_description>",
            "reflection_prompt_template": "Reflect template {prompt_template} {inputs_outputs_feedback}",
            "reflect_instance_template": "<pr_description>{{task}}</pr_description>",
            "nrpv_block": "## Navigation\n## Reproduction\n## Patch\n## Validation",
        },
        "docker": {
            "image_builder_script": "./scripts/build.sh",
            "workdir": "/testbed",
            "timeout": 30,
        },
        "agent": {
            "max_steps": 30,
            "cost_limit": 3.0,
            "timeout": 1800,
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
        assert config.system.dataset == "SWE-bench/SWE-bench_Verified"
        assert config.system.instances == ["astropy__astropy-12907"]
        assert config.system.output_dir == "./output"
        assert config.prompts.plan_generation_prompt == "Plan prompt here"
        assert config.prompts.plan_instance_template == "<pr_description>{{task}}</pr_description>"
        assert config.prompts.code_generation_prompt == "Code prompt here"
        assert config.prompts.code_instance_template == "<pr_description>{{task}}</pr_description>"
        assert (
            config.prompts.reflection_prompt_template
            == "Reflect template {prompt_template} {inputs_outputs_feedback}"
        )
        assert config.prompts.reflect_instance_template == "<pr_description>{{task}}</pr_description>"
        assert config.prompts.nrpv_block == "## Navigation\n## Reproduction\n## Patch\n## Validation"
        assert config.docker.image_builder_script == "./scripts/build.sh"
        assert config.docker.workdir == "/testbed"
        assert config.docker.timeout == 30
        assert config.docker.delete_images_after_instance is True
        assert config.docker.min_free_gb == 20
        assert config.docker.max_cached_images == 75
        assert config.docker.polybench_build_fallback is True
        assert config.docker.polybench_pull_timeout == 600
        assert config.docker.polybench_build_timeout == 3600
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
        assert config.agent.timeout == 1800
        assert config.api_key == "test-key-123"

    def test_api_key_injected(self, monkeypatch, config_file: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "my-secret-key")
        config = load_config(config_file)
        assert config.api_key == "my-secret-key"

    def test_loads_docker_storage_controls(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {
            "docker": {
                "delete_images_after_instance": False,
                "min_free_gb": 42,
                "max_cached_images": 99,
            }
        }
        filepath = tmp_path / "docker_storage.yaml"
        _write_test_config(filepath, data)

        config = load_config(filepath)

        assert config.docker.delete_images_after_instance is False
        assert config.docker.min_free_gb == 42
        assert config.docker.max_cached_images == 99


class TestLoadConfigValidation:
    def test_n_less_than_one_raises_fatal_error(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"n": 0}}
        filepath = tmp_path / "bad_config.yaml"
        _write_test_config(filepath, data)

        with pytest.raises(FatalError, match="n.*must be >= 1"):
            load_config(filepath)

    def test_negative_n_raises_fatal_error(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"n": -1}}
        filepath = tmp_path / "bad_config.yaml"
        _write_test_config(filepath, data)

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
        _write_test_config(filepath, data)

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
        _write_test_config(filepath, data)

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
        _write_test_config(filepath, data)

        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)

        assert config.system.resume.enabled is True
        assert "from_round < 1" in caplog.text


class TestReflectionTemplateNoFallback:
    """The reflection template is now sourced exclusively from config.yaml.
    There is no Python-side default — an empty/missing field stays empty,
    and the reflect agent will fail loudly when it tries to render a
    blank template.
    """

    def test_missing_template_stays_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        # Empty config — no prompts section at all
        filepath = tmp_path / "no_prompts.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == ""

    def test_empty_string_stays_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"prompts": {"reflection_prompt_template": ""}}
        filepath = tmp_path / "empty_tpl.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == ""

    def test_user_template_passed_through(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        custom = "MY-CUSTOM {prompt_template} {inputs_outputs_feedback}"
        data = {"prompts": {"reflection_prompt_template": custom}}
        filepath = tmp_path / "custom_tpl.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.prompts.reflection_prompt_template == custom


class TestSkipCompletedRounds:
    """``skip_completed_rounds`` controls whether the pipeline exits the
    round loop early once an instance is resolved. Default is ``True``
    so already-solved instances skip the remaining rounds; set to
    ``False`` to run all n rounds regardless of resolved status.
    """

    def test_default_is_true(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.system.skip_completed_rounds is True

    def test_explicit_false_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"skip_completed_rounds": False}}
        filepath = tmp_path / "skip_false.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.skip_completed_rounds is False

    def test_string_false_coerced(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"skip_completed_rounds": "false"}}
        filepath = tmp_path / "skip_str.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.skip_completed_rounds is False


class TestApiBaseAndTimeout:
    def test_custom_api_base(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "https://custom.example.com"}}
        filepath = tmp_path / "api_config.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.api_base == "https://custom.example.com"

    def test_custom_agent_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"timeout": 60}}
        filepath = tmp_path / "timeout_config.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.agent.timeout == 60


class TestAgentValidation:
    def test_max_steps_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"max_steps": 0}}
        filepath = tmp_path / "bad_agent.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="max_steps.*must be >= 1"):
            load_config(filepath)

    def test_cost_limit_negative_defaults_to_zero(self, monkeypatch, tmp_path: Path, caplog):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"cost_limit": -1.0}}
        filepath = tmp_path / "warn_agent.yaml"
        _write_test_config(filepath, data)
        with caplog.at_level(logging.WARNING):
            config = load_config(filepath)
        assert config.agent.cost_limit == 0.0

    def test_timeout_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"agent": {"timeout": 0}}
        filepath = tmp_path / "bad_agent.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="timeout.*must be >= 1"):
            load_config(filepath)


class TestDockerValidation:
    def test_docker_timeout_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"docker": {"timeout": 0}}
        filepath = tmp_path / "bad_docker.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="timeout.*must be >= 1"):
            load_config(filepath)


class TestApiBaseValidation:
    def test_invalid_api_base_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "not-a-url"}}
        filepath = tmp_path / "bad_api.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="Invalid api_base URL"):
            load_config(filepath)

    def test_missing_scheme_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"api_base": "deepseek.com"}}
        filepath = tmp_path / "bad_api.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="Invalid api_base URL"):
            load_config(filepath)


class TestEvaluatorConfig:
    def test_default_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.evaluator.timeout == 1800

    def test_custom_timeout(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": 600}}
        filepath = tmp_path / "eval_config.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.evaluator.timeout == 600

    def test_zero_timeout_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": 0}}
        filepath = tmp_path / "bad_eval.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="evaluator.timeout.*must be >= 1"):
            load_config(filepath)

    def test_negative_timeout_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"evaluator": {"timeout": -10}}
        filepath = tmp_path / "bad_eval.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="evaluator.timeout.*must be >= 1"):
            load_config(filepath)


class TestDefaults:
    def test_empty_config_uses_defaults(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty_config.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)

        assert config.system.n == 3
        assert config.system.model == "deepseek-v4-flash"
        assert config.system.api_base == "https://api.deepseek.com"
        assert config.system.optimization_info_level == 1
        assert config.system.dataset == "SWE-bench/SWE-bench_Verified"
        assert config.system.instances == []
        assert config.docker.workdir == "/testbed"
        assert config.agent.max_steps == 30
        assert config.agent.cost_limit == 3.0
        assert config.agent.timeout == 120


class TestDataset:
    """``system.dataset`` selects the SWE-bench dataset name passed to
    ``load_swebench_dataset(name=...)``. Phase 1 default is Verified;
    Phase 2 will override to SWE-bench/SWE-bench_Pro. The instance list
    is interpreted within the chosen dataset.
    """

    def test_dataset_default_is_verified(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.system.dataset == "SWE-bench/SWE-bench_Verified"

    def test_custom_dataset_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"dataset": "SWE-bench/SWE-bench_Pro"}}
        filepath = tmp_path / "pro.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.dataset == "SWE-bench/SWE-bench_Pro"

    def test_dataset_type_default_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.system.dataset_type == ""

    def test_dataset_type_polybench_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"dataset_type": "polybench"}}
        filepath = tmp_path / "polybench_type.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.dataset_type == "polybench"

    def test_language_filter_default_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.system.language_filter == ""

    def test_language_filter_python_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"language_filter": "Python"}}
        filepath = tmp_path / "lang_filter.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.system.language_filter == "Python"


class TestBatchIdValidation:
    """``system.batch_id`` is the folder segment between dataset and instance
    in the output tree: ``output/<dataset>/<batch_id>/<instance>/``.

    The field is *mandatory at load time* — the loader raises FatalError on
    missing, empty, or character-invalid values. These tests opt out of the
    ``_write_test_config`` helper (which injects a default) and write YAML
    directly so they exercise the validator's failure paths.
    """

    def test_load_config_missing_batch_id_fails(self, monkeypatch, tmp_path: Path):
        """No ``system.batch_id`` key at all → FatalError."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        # Note: NOT going through _write_test_config — we want a config
        # with an entirely absent batch_id field.
        data = {"system": {"n": 3}}
        filepath = tmp_path / "no_batch.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="batch_id is required"):
            load_config(filepath)

    def test_load_config_empty_batch_id_fails(self, monkeypatch, tmp_path: Path):
        """Explicit ``batch_id: ""`` (or whitespace-only) → FatalError."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"batch_id": "   "}}
        filepath = tmp_path / "empty_batch.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="batch_id is required"):
            load_config(filepath)

    def test_load_config_invalid_batch_id_fails(self, monkeypatch, tmp_path: Path):
        """Path-traversal / illegal characters are rejected.

        The whitelist is ``[A-Za-z0-9_.-]`` plus an explicit ``"."`` / ``".."``
        block so that the value can be used directly as a directory name
        without escaping or normalisation.
        """
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        # Slash is the canonical path-traversal vector; "../etc" combines a
        # parent-dir hop with a separator and exercises both guards.
        data = {"system": {"batch_id": "../etc"}}
        filepath = tmp_path / "bad_batch.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(FatalError, match="illegal characters"):
            load_config(filepath)

    def test_load_config_valid_batch_id(self, monkeypatch, tmp_path: Path):
        """A valid batch_id loads through and is preserved verbatim."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"system": {"batch_id": "run3_level1_n3"}}
        filepath = tmp_path / "good_batch.yaml"
        filepath.write_text(yaml.dump(data), encoding="utf-8")
        config = load_config(filepath)
        assert config.system.batch_id == "run3_level1_n3"


class TestAnalysisConfig:
    """``analysis.enable_review`` controls whether the watchdog runs the
    LLM-based quality review and rework loop after each analysis phase.
    Default is ``False`` so that review is opt-in.
    """

    def test_enable_review_default_is_false(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.analysis.enable_review is False

    def test_backend_default_is_mini_swe(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.analysis.backend == "mini_swe"

    def test_backend_opencode_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {
            "analysis": {
                "backend": "opencode",
                "model": "kimi-for-coding/k2p6",
                "opencode_isolate_per_case": False,
                "opencode_timeout": 123,
                "rate_limit_sleep_seconds": 456,
                "max_retries": 1,
            }
        }
        filepath = tmp_path / "opencode.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.backend == "opencode"
        assert config.analysis.model == "kimi-for-coding/k2p6"
        assert config.analysis.opencode_isolate_per_case is False
        assert config.analysis.opencode_timeout == 123
        assert config.analysis.rate_limit_sleep_seconds == 456
        assert config.analysis.max_retries == 1

    def test_opencode_isolate_per_case_default_true(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"backend": "opencode"}}
        filepath = tmp_path / "opencode_default.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.opencode_isolate_per_case is True

    def test_invalid_backend_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"backend": "bad"}}
        filepath = tmp_path / "bad_backend.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="Invalid analysis.backend"):
            load_config(filepath)

    def test_negative_opencode_retries_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"backend": "opencode", "max_retries": -1}}
        filepath = tmp_path / "bad_retries.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError, match="analysis.max_retries.*must be >= 0"):
            load_config(filepath)

    def test_enable_review_explicit_true(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"enable_review": True}}
        filepath = tmp_path / "review_on.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.enable_review is True

    def test_enable_review_string_true_coerced(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"enable_review": "true"}}
        filepath = tmp_path / "review_str.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.enable_review is True

    def test_enable_review_string_false_coerced(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"enable_review": "false"}}
        filepath = tmp_path / "review_str_false.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.enable_review is False

    def test_model_family_default_is_auto(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.analysis.model_family == "auto"

    def test_model_family_explicit_value(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"model_family": "kimi"}}
        filepath = tmp_path / "family_kimi.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.model_family == "kimi"

    def test_system_prompt_suffix_default_empty(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.analysis.system_prompt_suffix == ""

    def test_system_prompt_suffix_custom_value(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"analysis": {"system_prompt_suffix": "Always use bash."}}
        filepath = tmp_path / "suffix.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.analysis.system_prompt_suffix == "Always use bash."


class TestCheckerConfig:
    def test_default_disabled(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        filepath = tmp_path / "empty.yaml"
        _write_test_config(filepath)
        config = load_config(filepath)
        assert config.checker.enabled is False
        assert config.checker.model == "deepseek-v4-flash"
        assert config.checker.max_steps == 50

    def test_explicit_enabled(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"checker": {"enabled": True, "model": "deepseek-v4-pro", "max_steps": 100}}
        filepath = tmp_path / "checker.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.checker.enabled is True
        assert config.checker.model == "deepseek-v4-pro"
        assert config.checker.max_steps == 100

    def test_custom_rules_path(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"checker": {"rules_path": "./custom/rules.json"}}
        filepath = tmp_path / "rules.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.checker.rules_path == "./custom/rules.json"

    def test_cost_limit_validation(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"checker": {"cost_limit": -1.0}}
        filepath = tmp_path / "neg_cost.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.checker.cost_limit == 0.0

    def test_max_steps_zero_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"checker": {"max_steps": 0}}
        filepath = tmp_path / "zero_steps.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError):
            load_config(filepath)

    def test_invalid_api_base_raises(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"checker": {"api_base": "not-a-url"}}
        filepath = tmp_path / "bad_api.yaml"
        _write_test_config(filepath, data)
        with pytest.raises(FatalError):
            load_config(filepath)

    def test_prompts_check_loaded(self, monkeypatch, tmp_path: Path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        data = {"prompts": {"check_prompt": "You are a checker.", "check_instance_template": "Task: {{task}}"}}
        filepath = tmp_path / "prompts.yaml"
        _write_test_config(filepath, data)
        config = load_config(filepath)
        assert config.prompts.check_prompt == "You are a checker."
        assert config.prompts.check_instance_template == "Task: {{task}}"
