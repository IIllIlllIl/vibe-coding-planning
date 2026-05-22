"""Tests for src/analysis/evaluate_rules.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.evaluate_rules import evaluate_rules, load_rules


class TestLoadRules:
    def test_loads_valid_jsonl(self, tmp_path: Path):
        path = tmp_path / "rules.jsonl"
        with path.open("w") as f:
            f.write(json.dumps({"instance_id": "a", "rule": "r1"}) + "\n")
            f.write(json.dumps({"instance_id": "b", "rule": "r2"}) + "\n")
        rules = load_rules(path)
        assert len(rules) == 2
        assert rules[0]["instance_id"] == "a"

    def test_skips_empty_lines(self, tmp_path: Path):
        path = tmp_path / "rules.jsonl"
        with path.open("w") as f:
            f.write(json.dumps({"instance_id": "a", "rule": "r1"}) + "\n")
            f.write("\n")
            f.write(json.dumps({"instance_id": "b", "rule": "r2"}) + "\n")
        rules = load_rules(path)
        assert len(rules) == 2

    def test_skips_malformed_json(self, tmp_path: Path):
        path = tmp_path / "rules.jsonl"
        with path.open("w") as f:
            f.write("not json\n")
            f.write(json.dumps({"instance_id": "a", "rule": "r1"}) + "\n")
        rules = load_rules(path)
        assert len(rules) == 1


class TestEvaluateRules:
    def test_basic_statistics(self):
        rules = [
            {"instance_id": "a", "rule": "When X, do Y because Z.", "rule_valid": True},
            {"instance_id": "b", "rule": "When A, do B because C.", "rule_valid": True},
            {"instance_id": "c", "rule": "bad rule", "rule_valid": False},
        ]
        report = evaluate_rules(rules)
        assert report["total_cases"] == 3
        assert report["valid_format"] == 2
        assert report["invalid_format"] == 1
        assert report["valid_rate"] == pytest.approx(2 / 3)

    def test_empty_rules(self):
        report = evaluate_rules([])
        assert report["total_cases"] == 0
        assert report["valid_rate"] == 0

    def test_anti_pattern_filename(self):
        rules = [
            {"instance_id": "a", "rule": "When X, edit test_foo.py because Y.", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["anti_patterns"]["contains_filename"] == 1

    def test_anti_pattern_function_name(self):
        rules = [
            {"instance_id": "a", "rule": "When X, define function my_func() because Y.", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["anti_patterns"]["contains_function_name"] == 1

    def test_anti_pattern_line_number(self):
        rules = [
            {"instance_id": "a", "rule": "When X, fix line 42 because Y.", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["anti_patterns"]["contains_line_number"] == 1

    def test_anti_pattern_too_short(self):
        rules = [
            {"instance_id": "a", "rule": "X", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["anti_patterns"]["too_short"] == 1

    def test_anti_pattern_too_long(self):
        rules = [
            {"instance_id": "a", "rule": "x" * 2001, "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["anti_patterns"]["too_long"] == 1

    def test_rule_count_distribution(self):
        rules = [
            {"instance_id": "a", "rule": "When X, do Y because Z.\nWhen A, do B because C.", "rule_valid": True},
            {"instance_id": "b", "rule": "When X, do Y because Z.", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert report["avg_rules_per_case"] == pytest.approx(1.5)
        assert report["rule_count_distribution"][2] == 1
        assert report["rule_count_distribution"][1] == 1

    def test_no_anti_patterns_for_clean_rule(self):
        rules = [
            {"instance_id": "a", "rule": "When the plan assumes a single root cause without verifying alternatives, systematically enumerate and test each plausible cause because premature focus on one hypothesis blinds the analyst to the actual fault.", "rule_valid": True},
        ]
        report = evaluate_rules(rules)
        assert not report["anti_patterns"]
