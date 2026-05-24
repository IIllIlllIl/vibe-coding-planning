"""Tests for src/analysis/aggregation_agent.py.

Covers rule loading, prompt construction, JSON extraction from LLM output,
result validation, and end-to-end aggregation with mocked LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.analysis import aggregation_agent as agg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_per_case_dir(tmp_path: Path) -> Path:
    """Create a temporary per_case directory with sample rule files."""
    per_case = tmp_path / "per_case"
    per_case.mkdir()

    # Valid single-rule case
    (per_case / "case_a.json").write_text(
        json.dumps(
            {
                "instance_id": "case_a",
                "rule": "When the plan misses edge cases, add boundary tests because tests encode expected behavior.",
                "rule_valid": True,
            }
        ),
        encoding="utf-8",
    )

    # Valid multi-rule case
    (per_case / "case_b.json").write_text(
        json.dumps(
            {
                "instance_id": "case_b",
                "rule": (
                    "When the bug involves regex, check Unicode support because ASCII-only assumptions fail on international input.\n"
                    "When a test exists for the bug, read its assertions because they encode expected behavior."
                ),
                "rule_valid": True,
            }
        ),
        encoding="utf-8",
    )

    # Invalid rule (should be skipped)
    (per_case / "case_c.json").write_text(
        json.dumps(
            {
                "instance_id": "case_c",
                "rule": "Some garbage without proper format.",
                "rule_valid": False,
            }
        ),
        encoding="utf-8",
    )

    # Valid but with non-rule lines mixed in
    (per_case / "case_d.json").write_text(
        json.dumps(
            {
                "instance_id": "case_d",
                "rule": (
                    "Introductory text that is not a rule.\n"
                    "When the fix changes public API, update documentation because users depend on accurate docs.\n"
                    "More fluff here."
                ),
                "rule_valid": True,
            }
        ),
        encoding="utf-8",
    )

    # Valid but empty rule text
    (per_case / "case_e.json").write_text(
        json.dumps(
            {
                "instance_id": "case_e",
                "rule": "",
                "rule_valid": True,
            }
        ),
        encoding="utf-8",
    )

    # Malformed JSON (should be skipped with warning)
    (per_case / "case_f.json").write_text("not valid json", encoding="utf-8")

    return per_case


# ---------------------------------------------------------------------------
# load_rules
# ---------------------------------------------------------------------------

class TestLoadRules:
    def test_loads_valid_rules(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        texts = [r["text"] for r in rules]

        assert len(rules) == 4  # case_a(1) + case_b(2) + case_d(1)
        assert any("edge cases" in t for t in texts)
        assert any("regex" in t for t in texts)
        assert any("test exists" in t for t in texts)
        assert any("public API" in t for t in texts)

    def test_skips_invalid_rule_valid(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        texts = [r["text"] for r in rules]
        assert not any("garbage" in t for t in texts)

    def test_skips_empty_rule_text(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        assert len(rules) == 4

    def test_skips_malformed_json(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        assert len(rules) == 4  # case_f is skipped

    def test_splits_multi_line_rules(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        case_b_rules = [r for r in rules if r["instance_id"] == "case_b"]
        assert len(case_b_rules) == 2

    def test_filters_non_when_lines(self, sample_per_case_dir: Path):
        rules = agg.load_rules(sample_per_case_dir)
        case_d_rules = [r for r in rules if r["instance_id"] == "case_d"]
        assert len(case_d_rules) == 1
        assert "public API" in case_d_rules[0]["text"]

    def test_fallback_whole_block_when_no_when_lines(self, sample_per_case_dir: Path):
        """If no line starts with 'When ', the whole block is treated as one rule."""
        per_case = sample_per_case_dir
        (per_case / "case_g.json").write_text(
            json.dumps(
                {
                    "instance_id": "case_g",
                    "rule": "A single sentence without When prefix but long enough.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )
        rules = agg.load_rules(per_case)
        case_g_rules = [r for r in rules if r["instance_id"] == "case_g"]
        assert len(case_g_rules) == 1

    def test_skips_too_short_rules(self, sample_per_case_dir: Path):
        per_case = sample_per_case_dir
        (per_case / "case_h.json").write_text(
            json.dumps(
                {
                    "instance_id": "case_h",
                    "rule": "When short.\nWhen this is a much longer rule that should be kept because it has a full justification clause.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )
        rules = agg.load_rules(per_case)
        case_h_rules = [r for r in rules if r["instance_id"] == "case_h"]
        assert len(case_h_rules) == 1
        assert "much longer" in case_h_rules[0]["text"]

    def test_empty_dir(self, tmp_path: Path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        rules = agg.load_rules(empty_dir)
        assert rules == []

    def test_nonexistent_dir(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            agg.load_rules(tmp_path / "does_not_exist")

    def test_instance_id_fallback_to_stem(self, sample_per_case_dir: Path):
        """If instance_id is missing, fall back to filename stem."""
        (sample_per_case_dir / "fallback.json").write_text(
            json.dumps(
                {
                    "rule": "When fallback occurs, use stem because filenames are reliable.",
                    "rule_valid": True,
                }
            ),
            encoding="utf-8",
        )
        rules = agg.load_rules(sample_per_case_dir)
        fb = [r for r in rules if r["instance_id"] == "fallback"]
        assert len(fb) == 1


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt:
    def test_includes_all_rules(self):
        rules = [
            {"text": "When A, do X because Y.", "instance_id": "i1", "raw_index": 0},
            {"text": "When B, do Y because Z.", "instance_id": "i2", "raw_index": 0},
        ]
        prompt = agg.build_user_prompt(rules)
        assert "1. When A, do X because Y." in prompt
        assert "2. When B, do Y because Z." in prompt
        assert "You are given 2 contrastive rules" in prompt

    def test_escapes_backslashes(self):
        rules = [
            {"text": "When regex uses \\d, check Unicode because ASCII-only fails.", "instance_id": "i1", "raw_index": 0},
        ]
        prompt = agg.build_user_prompt(rules)
        assert "/d" in prompt  # backslash replaced with forward slash
        assert "\\d" not in prompt

    def test_rule_count_placeholder(self):
        rules = [{"text": "When A, do X because Y.", "instance_id": "i1", "raw_index": 0}]
        prompt = agg.build_user_prompt(rules)
        assert "You are given 1 contrastive rules" in prompt


# ---------------------------------------------------------------------------
# _extract_json_from_text
# ---------------------------------------------------------------------------

class TestExtractJsonFromText:
    def test_plain_json(self):
        text = '{"always": ["r1"], "branches": []}'
        result = agg._extract_json_from_text(text)
        assert result == {"always": ["r1"], "branches": []}

    def test_json_in_markdown_fence(self):
        text = "```json\n{\"always\": [\"r1\"], \"branches\": []}\n```"
        result = agg._extract_json_from_text(text)
        assert result == {"always": ["r1"], "branches": []}

    def test_json_in_generic_fence(self):
        text = "Some intro\n```\n{\"always\": [\"r1\"], \"branches\": []}\n```\nOutro"
        result = agg._extract_json_from_text(text)
        assert result == {"always": ["r1"], "branches": []}

    def test_text_before_and_after_json(self):
        text = "Here is the result:\n{\"always\": [\"r1\"], \"branches\": []}\nDone!"
        result = agg._extract_json_from_text(text)
        assert result == {"always": ["r1"], "branches": []}

    def test_nested_json(self):
        text = '{"always": [], "branches": [{"condition": "c1", "rules": ["r1"]}]}'
        result = agg._extract_json_from_text(text)
        assert result["branches"][0]["condition"] == "c1"

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object"):
            agg._extract_json_from_text("Just plain text here.")

    def test_unclosed_json_raises(self):
        with pytest.raises(ValueError, match="Unclosed JSON"):
            agg._extract_json_from_text('{"always": ["r1", ')

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            agg._extract_json_from_text('{"always": invalid}')


# ---------------------------------------------------------------------------
# _validate_aggregation_result
# ---------------------------------------------------------------------------

class TestValidateAggregationResult:
    def test_valid_result(self):
        data = {
            "always": ["r1", "r2"],
            "branches": [
                {"condition": "c1", "rules": ["r3"]},
                {"condition": "c2", "rules": ["r4", "r5"]},
            ],
        }
        result = agg._validate_aggregation_result(data)
        assert result["always"] == ["r1", "r2"]
        assert len(result["branches"]) == 2
        assert result["branches"][0]["condition"] == "c1"
        assert result["branches"][0]["rules"] == ["r3"]

    def test_missing_always_defaults_to_empty(self):
        data = {"branches": [{"condition": "c1", "rules": ["r1"]}]}
        result = agg._validate_aggregation_result(data)
        assert result["always"] == []

    def test_missing_branches_defaults_to_empty(self):
        data = {"always": ["r1"]}
        result = agg._validate_aggregation_result(data)
        assert result["branches"] == []

    def test_branch_missing_condition_raises(self):
        data = {"branches": [{"rules": ["r1"]}]}
        with pytest.raises(ValueError, match="missing 'condition'"):
            agg._validate_aggregation_result(data)

    def test_branch_missing_rules_raises(self):
        data = {"branches": [{"condition": "c1"}]}
        with pytest.raises(ValueError, match="missing 'rules'"):
            agg._validate_aggregation_result(data)

    def test_strips_empty_strings(self):
        data = {
            "always": ["r1", "", "r2"],
            "branches": [
                {"condition": "c1", "rules": ["r3", ""]},
            ],
        }
        result = agg._validate_aggregation_result(data)
        assert result["always"] == ["r1", "r2"]
        assert result["branches"][0]["rules"] == ["r3"]

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="Expected dict"):
            agg._validate_aggregation_result([1, 2, 3])

    def test_invalid_always_type_raises(self):
        data = {"always": "not a list"}
        with pytest.raises(ValueError, match="'always' must be a list"):
            agg._validate_aggregation_result(data)

    def test_invalid_branch_type_raises(self):
        data = {"branches": ["not a dict"]}
        with pytest.raises(ValueError, match="Branch 0 must be a dict"):
            agg._validate_aggregation_result(data)


# ---------------------------------------------------------------------------
# _call_litellm (mocked)
# ---------------------------------------------------------------------------

class TestCallLitellm:
    def test_deepseek_prefix_added(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"always": [], "branches": []}'

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            result = agg._call_litellm(
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
                system_prompt="sys",
                user_prompt="user",
            )
            assert result == '{"always": [], "branches": []}'
            call_kwargs = mock_completion.call_args.kwargs
            assert call_kwargs["model"] == "deepseek/deepseek-v4-pro"
            assert call_kwargs["api_key"] == "test-key"
            assert call_kwargs["api_base"] == "https://api.deepseek.com"

    def test_prefixed_model_unchanged(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            agg._call_litellm(
                model_name="moonshot/kimi-k2.6",
                api_key="test-key",
                api_base="https://api.moonshot.cn",
                system_prompt="sys",
                user_prompt="user",
            )
            assert mock_completion.call_args.kwargs["model"] == "moonshot/kimi-k2.6"

    def test_empty_content_handled(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""

        with patch("litellm.completion", return_value=mock_response):
            result = agg._call_litellm(
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
                system_prompt="sys",
                user_prompt="user",
            )
            assert result == ""


# ---------------------------------------------------------------------------
# aggregate (end-to-end with mocked LLM)
# ---------------------------------------------------------------------------

class TestAggregate:
    def test_successful_aggregation(self, sample_per_case_dir: Path, tmp_path: Path):
        llm_output = json.dumps(
            {
                "always": ["When universal, do X because Y."],
                "branches": [
                    {"condition": "Bug involves regex", "rules": ["When regex, check Unicode because ASCII fails."]}
                ],
            }
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        output_path = tmp_path / "result.json"

        with patch("litellm.completion", return_value=mock_response):
            result = agg.aggregate(
                per_case_dir=sample_per_case_dir,
                output_path=output_path,
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
            )

        assert result["always"] == ["When universal, do X because Y."]
        assert len(result["branches"]) == 1
        assert output_path.exists()
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert saved["always"] == result["always"]
        assert "_meta" in saved
        assert saved["_meta"]["source_rule_count"] == 4

    def test_no_valid_rules_raises(self, tmp_path: Path):
        empty_dir = tmp_path / "empty_per_case"
        empty_dir.mkdir()

        with pytest.raises(ValueError, match="No valid rules"):
            agg.aggregate(
                per_case_dir=empty_dir,
                output_path=tmp_path / "out.json",
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
            )

    def test_llm_returns_markdown_fenced_json(self, sample_per_case_dir: Path, tmp_path: Path):
        llm_output = (
            "Here is the aggregated result:\n"
            "```json\n"
            + json.dumps({"always": ["r1"], "branches": []})
            + "\n```"
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        with patch("litellm.completion", return_value=mock_response):
            result = agg.aggregate(
                per_case_dir=sample_per_case_dir,
                output_path=tmp_path / "out.json",
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
            )
        assert result["always"] == ["r1"]

    def test_llm_returns_invalid_json_raises(self, sample_per_case_dir: Path, tmp_path: Path):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Not JSON at all"

        with patch("litellm.completion", return_value=mock_response):
            with pytest.raises(ValueError, match="No JSON object"):
                agg.aggregate(
                    per_case_dir=sample_per_case_dir,
                    output_path=tmp_path / "out.json",
                    model_name="deepseek-v4-pro",
                    api_key="test-key",
                    api_base="https://api.deepseek.com",
                )

    def test_output_parent_dir_created(self, sample_per_case_dir: Path, tmp_path: Path):
        llm_output = json.dumps({"always": [], "branches": []})
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        nested_output = tmp_path / "deep" / "nested" / "result.json"

        with patch("litellm.completion", return_value=mock_response):
            agg.aggregate(
                per_case_dir=sample_per_case_dir,
                output_path=nested_output,
                model_name="deepseek-v4-pro",
                api_key="test-key",
                api_base="https://api.deepseek.com",
            )

        assert nested_output.exists()


# ---------------------------------------------------------------------------
# aggregate_with_config
# ---------------------------------------------------------------------------

class TestAggregateWithConfig:
    def test_uses_config_credentials(self, sample_per_case_dir: Path, tmp_path: Path):
        from src.config import AnalysisConfig, Config

        llm_output = json.dumps({"always": ["r1"], "branches": []})
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = llm_output

        config = Config(
            analysis=AnalysisConfig(
                model="deepseek-v4-pro",
                api_base="https://api.deepseek.com",
                api_key_env="TEST_API_KEY",
            ),
            analysis_api_key="cfg-key",
        )

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            agg.aggregate_with_config(
                per_case_dir=sample_per_case_dir,
                output_path=tmp_path / "out.json",
                config=config,
            )
            assert mock_completion.call_args.kwargs["api_key"] == "cfg-key"
            assert mock_completion.call_args.kwargs["api_base"] == "https://api.deepseek.com"

    def test_model_override(self, sample_per_case_dir: Path, tmp_path: Path):
        from src.config import AnalysisConfig, Config

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({"always": [], "branches": []})

        config = Config(
            analysis=AnalysisConfig(
                model="deepseek-v4-flash",
                api_base="https://api.deepseek.com",
            ),
            analysis_api_key="key",
        )

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            agg.aggregate_with_config(
                per_case_dir=sample_per_case_dir,
                output_path=tmp_path / "out.json",
                config=config,
                model_override="deepseek-v4-pro",
            )
            assert mock_completion.call_args.kwargs["model"] == "deepseek/deepseek-v4-pro"

    def test_missing_api_key_raises(self, sample_per_case_dir: Path, tmp_path: Path):
        from src.config import AnalysisConfig, Config

        config = Config(
            analysis=AnalysisConfig(
                model="deepseek-v4-pro",
                api_base="https://api.deepseek.com",
                api_key_env="MISSING_KEY_ENV",
            ),
            analysis_api_key="",  # empty
        )

        with pytest.raises(RuntimeError, match="Analysis API key not set"):
            agg.aggregate_with_config(
                per_case_dir=sample_per_case_dir,
                output_path=tmp_path / "out.json",
                config=config,
            )
