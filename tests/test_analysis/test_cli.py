"""Tests for src/analysis/cli.py rule validation and aggregation logic."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.analysis.cli import main


def _validate_rule(rule_text: str) -> bool:
    """Mirror of the validation logic in cli.py:108-111."""
    lines = [ln.strip() for ln in rule_text.splitlines() if ln.strip()]
    rule_lines = [ln for ln in lines if ln.lower().startswith("when ")]
    return bool(rule_lines) and all(" because " in ln.lower() for ln in rule_lines)


class TestRuleValidation:
    def test_single_valid_rule(self):
        text = "When the plan misses edge cases, add boundary tests because tests encode expected behavior."
        assert _validate_rule(text) is True

    def test_multiple_valid_rules(self):
        text = (
            "When the plan misses edge cases, add boundary tests because tests encode expected behavior.\n"
            "When a fix is too broad, narrow the scope because unintended changes cause regressions."
        )
        assert _validate_rule(text) is True

    def test_missing_because(self):
        text = "When the plan misses edge cases, add boundary tests."
        assert _validate_rule(text) is False

    def test_missing_when_prefix(self):
        text = "The plan misses edge cases, add boundary tests because tests encode expected behavior."
        assert _validate_rule(text) is False

    def test_empty_text(self):
        assert _validate_rule("") is False

    def test_only_whitespace(self):
        assert _validate_rule("   \n  \t  ") is False

    def test_mixed_valid_and_invalid_lines(self):
        """At least one valid rule line required."""
        text = (
            "Some intro text here.\n"
            "When the plan misses edge cases, add boundary tests because tests encode expected behavior.\n"
            "Another invalid line."
        )
        assert _validate_rule(text) is True

    def test_all_invalid_lines(self):
        text = (
            "Some intro text here.\n"
            "Another invalid line.\n"
            "No rules at all."
        )
        assert _validate_rule(text) is False

    def test_case_insensitive_when(self):
        text = "WHEN the plan misses edge cases, add tests BECAUSE behavior matters."
        assert _validate_rule(text) is True

    def test_case_insensitive_because(self):
        text = "When the plan misses edge cases, add tests BECAUSE behavior matters."
        assert _validate_rule(text) is True

    def test_markdown_headers_ignored(self):
        text = (
            "# Rule 1\n"
            "When the plan misses edge cases, add boundary tests because tests encode expected behavior.\n"
            "## Rule 2\n"
            "When a fix is too broad, narrow the scope because unintended changes cause regressions."
        )
        assert _validate_rule(text) is True


# ---------------------------------------------------------------------------
# --aggregate CLI tests
# ---------------------------------------------------------------------------

def _write_minimal_config(path: Path) -> None:
    """Write a minimal valid config.yaml for CLI tests."""
    import yaml

    cfg = {
        "system": {"batch_id": "test-batch"},
        "analysis": {
            "output_dir": "./output",
            "api_key_env": "TEST_AGG_KEY",
        },
    }
    path.write_text(yaml.dump(cfg), encoding="utf-8")


class TestAggregateFlag:
    def test_aggregate_runs_successfully(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TEST_AGG_KEY", "test-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        per_case = tmp_path / "per_case"
        per_case.mkdir()
        (per_case / "case_1.json").write_text(
            json.dumps(
                {
                    "instance_id": "case_1",
                    "rule": "When A, do X because Y.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )

        config_path = tmp_path / "config.yaml"
        _write_minimal_config(config_path)

        output_dir = tmp_path / "output"

        llm_output = json.dumps(
            {
                "always": ["When universal, do X because Y."],
                "branches": [{"condition": "c1", "rules": ["When A, do X because Y."]}],
            }
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        monkeypatch.chdir(tmp_path)

        with patch("litellm.completion", return_value=mock_response):
            rc = main(
                [
                    "--config", str(config_path),
                    "--input", str(per_case),
                    "--output", str(output_dir),
                    "--aggregate",
                ]
            )

        assert rc == 0
        assert (output_dir / "aggregated_rules.json").exists()

    def test_aggregate_no_valid_rules_fails(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        per_case = tmp_path / "per_case"
        per_case.mkdir()

        config_path = tmp_path / "config.yaml"
        _write_minimal_config(config_path)

        monkeypatch.chdir(tmp_path)

        rc = main(
            [
                "--config", str(config_path),
                "--input", str(per_case),
                "--aggregate",
            ]
        )
        assert rc == 1

    def test_aggregate_with_model_override(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("TEST_AGG_KEY", "test-key")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        per_case = tmp_path / "per_case"
        per_case.mkdir()
        (per_case / "case_1.json").write_text(
            json.dumps(
                {
                    "instance_id": "case_1",
                    "rule": "When A, do X because Y.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )

        config_path = tmp_path / "config.yaml"
        _write_minimal_config(config_path)

        llm_output = json.dumps({"always": [], "branches": []})
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        monkeypatch.chdir(tmp_path)

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            rc = main(
                [
                    "--config", str(config_path),
                    "--input", str(per_case),
                    "--aggregate",
                    "--model", "deepseek-v4-pro",
                ]
            )

        assert rc == 0
        # api_base defaults to moonshot in test config, so no deepseek prefix is added
        assert mock_completion.call_args.kwargs["model"] == "deepseek-v4-pro"

    def test_aggregate_uses_opencode_backend(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        per_case = tmp_path / "per_case"
        per_case.mkdir()
        (per_case / "case_1.json").write_text(
            json.dumps(
                {
                    "instance_id": "case_1",
                    "rule": "When A, do X because Y.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "system:\n  batch_id: test-batch\nanalysis:\n  backend: opencode\n  output_dir: ./output\n",
            encoding="utf-8",
        )

        output_dir = tmp_path / "output"
        monkeypatch.chdir(tmp_path)

        expected = {
            "always": [],
            "branches": [{"condition": "c1", "rules": ["When A, do X because Y."]}],
        }
        with patch("src.analysis.cli.aggregate_with_opencode", return_value=expected) as mock_agg:
            rc = main(
                [
                    "--config", str(config_path),
                    "--input", str(per_case),
                    "--output", str(output_dir),
                    "--aggregate",
                ]
            )

        assert rc == 0
        assert mock_agg.called


class TestPostprocessFlag:
    def test_postprocess_runs_to_postprocessed_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        per_case = tmp_path / "per_case"
        per_case.mkdir()
        config_path = tmp_path / "config.yaml"
        _write_minimal_config(config_path)
        output_dir = tmp_path / "output"

        expected = {
            "copied_valid": 1,
            "repaired": 1,
            "failed": 0,
            "skipped_empty": 0,
        }
        with patch(
            "src.analysis.cli.postprocess_per_case_dir",
            return_value=expected,
        ) as mock_postprocess:
            rc = main(
                [
                    "--config", str(config_path),
                    "--input", str(per_case),
                    "--output", str(output_dir),
                    "--postprocess-data-dir", str(tmp_path / "cases"),
                    "--postprocess",
                ]
            )

        assert rc == 0
        assert mock_postprocess.call_args.kwargs["per_case_dir"] == per_case.resolve()
        assert (
            mock_postprocess.call_args.kwargs["output_dir"]
            == output_dir / "per_case_postprocessed"
        )
        assert (
            mock_postprocess.call_args.kwargs["data_base_dir"]
            == str(tmp_path / "cases")
        )


class TestExtractionBackendFlag:
    def test_extraction_uses_opencode_backend(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-ds-key")

        data_dir = tmp_path / "reflect_success_cases"
        case_dir = data_dir / "case__one-1"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "patches").mkdir()
        (case_dir / "trajectories").mkdir()
        (data_dir / "manifest.json").write_text(
            json.dumps({"cases": [{"instance_id": "case__one-1"}]}),
            encoding="utf-8",
        )
        (case_dir / "result.json").write_text(
            json.dumps(
                {
                    "plans": [
                        {
                            "round": 1,
                            "generated_by": "plan_agent",
                            "test_results": {"resolved": False},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (case_dir / "plans/plan_1_plan_gen_x.md").write_text("plan", encoding="utf-8")
        (case_dir / "patches/patch_1_x.patch").write_text("patch", encoding="utf-8")
        (case_dir / "trajectories/trajectory_1_plan_gen_x.json").write_text(
            "{}", encoding="utf-8"
        )

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "system:\n  batch_id: test-batch\nanalysis:\n  backend: opencode\n",
            encoding="utf-8",
        )
        output_dir = tmp_path / "output"
        monkeypatch.chdir(tmp_path)

        with patch(
            "src.analysis.cli.run_opencode_agent",
            return_value=("When A, do B because C.", [{"role": "assistant"}]),
        ) as mock_run:
            rc = main(
                [
                    "--config", str(config_path),
                    "--input", str(data_dir),
                    "--output", str(output_dir),
                ]
            )

        assert rc == 0
        assert mock_run.called
        result = json.loads((output_dir / "per_case/case__one-1.json").read_text())
        assert result["rule_valid"] is True
