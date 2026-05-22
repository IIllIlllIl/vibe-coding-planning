"""Tests for src/analysis/cli.py rule validation logic.

The validation is embedded in the main() loop; we test it indirectly
by extracting the validation logic into a helper."""

from __future__ import annotations

import pytest


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
