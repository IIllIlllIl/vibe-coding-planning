"""Tests for src.rules.rule_loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rules.rule_loader import format_rules_for_prompt, load_aggregated_rules


class TestLoadAggregatedRules:
    def test_loads_valid_file(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps(
                {
                    "always": ["When A, do X because Y."],
                    "branches": [
                        {
                            "condition": "Bug involves regex",
                            "rules": ["When regex, check Unicode because ASCII fails."],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        rules = load_aggregated_rules(path)
        assert rules["always"] == ["When A, do X because Y."]
        assert len(rules["branches"]) == 1
        assert rules["branches"][0]["condition"] == "Bug involves regex"

    def test_file_not_found_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_aggregated_rules(tmp_path / "nonexistent.json")

    def test_invalid_json_raises(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            load_aggregated_rules(path)

    def test_missing_always_defaults_to_empty(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps({"branches": [{"condition": "c", "rules": ["r"]}]}),
            encoding="utf-8",
        )
        rules = load_aggregated_rules(path)
        assert rules["always"] == []
        assert len(rules["branches"]) == 1

    def test_missing_branches_defaults_to_empty(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps({"always": ["r1"]}),
            encoding="utf-8",
        )
        rules = load_aggregated_rules(path)
        assert rules["always"] == ["r1"]
        assert rules["branches"] == []

    def test_branch_missing_condition_raises(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps(
                {
                    "always": [],
                    "branches": [{"rules": ["r1"]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing 'condition'"):
            load_aggregated_rules(path)

    def test_branch_missing_rules_raises(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text(
            json.dumps(
                {
                    "always": [],
                    "branches": [{"condition": "c"}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="missing 'rules'"):
            load_aggregated_rules(path)

    def test_non_dict_raises(self, tmp_path: Path):
        path = tmp_path / "rules.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON object"):
            load_aggregated_rules(path)


class TestFormatRulesForPrompt:
    def test_formats_always_rules(self):
        rules = {
            "always": ["When A, do X because Y.", "When B, do Z because W."],
            "branches": [],
        }
        text = format_rules_for_prompt(rules)
        assert "UNIVERSAL RULES" in text
        assert "1. When A, do X because Y." in text
        assert "2. When B, do Z because W." in text
        assert "CONDITIONAL RULES" not in text

    def test_formats_branches(self):
        rules = {
            "always": [],
            "branches": [
                {
                    "condition": "Bug involves regex",
                    "rules": ["When regex, check Unicode because ASCII fails."],
                }
            ],
        }
        text = format_rules_for_prompt(rules)
        assert "CONDITIONAL RULES" in text
        assert "--- Branch: Bug involves regex ---" in text
        assert "1. When regex, check Unicode because ASCII fails." in text
        assert "UNIVERSAL RULES" not in text

    def test_skips_empty_branches(self):
        rules = {
            "always": ["r1"],
            "branches": [
                {"condition": "c1", "rules": ["r2"]},
                {"condition": "c2", "rules": []},
            ],
        }
        text = format_rules_for_prompt(rules)
        assert "c1" in text
        assert "c2" not in text

    def test_empty_rules_returns_empty(self):
        rules = {"always": [], "branches": []}
        text = format_rules_for_prompt(rules)
        assert text == ""

    def test_combined_always_and_branches(self):
        rules = {
            "always": ["Always rule 1."],
            "branches": [
                {
                    "condition": "Condition A",
                    "rules": ["Branch rule 1.", "Branch rule 2."],
                }
            ],
        }
        text = format_rules_for_prompt(rules)
        assert "UNIVERSAL RULES" in text
        assert "CONDITIONAL RULES" in text
        assert "Always rule 1." in text
        assert "Branch rule 1." in text
        assert "Branch rule 2." in text
