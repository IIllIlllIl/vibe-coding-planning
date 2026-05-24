"""Tests for src/analysis/reviewer_agent.py.

Focus on JSON extraction from messages, result normalization, and prompt
structure.  Does not spawn real LLM agents.
"""

from __future__ import annotations

import pytest

from src.analysis.reviewer_agent import (
    REVIEWER_INSTANCE_TEMPLATE,
    REVIEWER_SYSTEM_TEMPLATE,
    _extract_review_from_messages,
    _normalize_review,
)


class TestExtractReviewFromMessages:
    def test_extracts_from_final_review_json_prefix(self):
        messages = [
            {
                "role": "assistant",
                "content": "I have evaluated the rule.\n\nFINAL_REVIEW_JSON: {\"passed\": true, \"score\": 85, \"feedback\": \"Good rule\", \"issues\": [], \"improvement_suggestions\": \"None\"}",
            }
        ]
        result = _extract_review_from_messages(messages)
        assert result == {
            "passed": True,
            "score": 85,
            "feedback": "Good rule",
            "issues": [],
            "improvement_suggestions": "None",
        }

    def test_extracts_from_code_block(self):
        messages = [
            {
                "role": "assistant",
                "content": 'Here is my review:\n```json\n{"passed": false, "score": 45, "feedback": "Too vague", "issues": ["No causality"], "improvement_suggestions": "Add because clause"}\n```',
            }
        ]
        result = _extract_review_from_messages(messages)
        assert result is not None
        assert result["passed"] is False
        assert result["score"] == 45

    def test_prefers_newer_message(self):
        messages = [
            {
                "role": "assistant",
                "content": "FINAL_REVIEW_JSON: {\"passed\": false, \"score\": 30, \"feedback\": \"Old\", \"issues\": [], \"improvement_suggestions\": \"\"}",
            },
            {
                "role": "user",
                "content": "Please fix",
            },
            {
                "role": "assistant",
                "content": "FINAL_REVIEW_JSON: {\"passed\": true, \"score\": 80, \"feedback\": \"New\", \"issues\": [], \"improvement_suggestions\": \"\"}",
            },
        ]
        result = _extract_review_from_messages(messages)
        assert result["passed"] is True
        assert result["score"] == 80

    def test_skips_non_assistant_messages(self):
        messages = [
            {"role": "user", "content": "FINAL_REVIEW_JSON: {\"passed\": true}"},
        ]
        assert _extract_review_from_messages(messages) is None

    def test_returns_none_when_no_json_found(self):
        messages = [
            {"role": "assistant", "content": "I think the rule is okay."},
        ]
        assert _extract_review_from_messages(messages) is None

    def test_extracts_from_plain_json_object(self):
        messages = [
            {
                "role": "assistant",
                "content": 'Some text before. {"passed": true, "score": 75, "feedback": "A", "issues": [], "improvement_suggestions": "B"} some text after.',
            }
        ]
        result = _extract_review_from_messages(messages)
        assert result is not None
        assert result["score"] == 75

    def test_extracts_multiline_final_review_json(self):
        """FINAL_REVIEW_JSON may appear not on the absolute last line."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    "Evaluation complete.\n"
                    'FINAL_REVIEW_JSON: {"passed":true,"score":90,"feedback":"Excellent","issues":[],"improvement_suggestions":"None"}\n'
                    "Have a nice day."
                ),
            }
        ]
        result = _extract_review_from_messages(messages)
        assert result is not None
        assert result["score"] == 90

    def test_extracts_from_first_code_block_with_passed_and_score(self):
        """Code blocks without passed+score are skipped."""
        messages = [
            {
                "role": "assistant",
                "content": (
                    "```bash\necho hello\n```\n"
                    '```json\n{"passed": true, "score": 88, "feedback": "G", "issues": [], "improvement_suggestions": "H"}\n```'
                ),
            }
        ]
        result = _extract_review_from_messages(messages)
        assert result is not None
        assert result["score"] == 88


class TestNormalizeReview:
    def test_perfect_result_unchanged(self):
        raw = {
            "passed": True,
            "score": 85,
            "feedback": "Good",
            "issues": [],
            "improvement_suggestions": "None",
        }
        result = _normalize_review(raw)
        assert result["passed"] is True
        assert result["score"] == 85
        assert result["feedback"] == "Good"
        assert result["issues"] == []
        assert result["improvement_suggestions"] == "None"

    def test_recalculates_passed_from_score(self):
        raw = {"passed": True, "score": 50}
        result = _normalize_review(raw)
        assert result["score"] == 50
        assert result["passed"] is False  # score < 70 overrides passed=True

    def test_sets_passed_when_score_high(self):
        raw = {"score": 75}
        result = _normalize_review(raw)
        assert result["passed"] is True

    def test_clamps_score_to_range(self):
        assert _normalize_review({"score": -10})["score"] == 0
        assert _normalize_review({"score": 150})["score"] == 100
        assert _normalize_review({"score": "bad"})["score"] == 0

    def test_handles_missing_fields(self):
        result = _normalize_review({"score": 80})
        assert result["passed"] is True
        assert result["feedback"] == ""
        assert result["issues"] == []
        assert result["improvement_suggestions"] == ""

    def test_filters_none_issues(self):
        raw = {"issues": ["a", None, "b", ""]}
        result = _normalize_review(raw)
        assert result["issues"] == ["a", "b", ""]

    def test_non_dict_returns_default(self):
        result = _normalize_review(None)
        assert result["passed"] is False
        assert result["score"] == 0

    def test_stringifies_feedback_and_suggestions(self):
        raw = {"feedback": 123, "improvement_suggestions": 456}
        result = _normalize_review(raw)
        assert result["feedback"] == "123"
        assert result["improvement_suggestions"] == "456"


class TestReviewerSystemTemplate:
    def test_contains_format_instruction(self):
        assert "FINAL_REVIEW_JSON:" in REVIEWER_SYSTEM_TEMPLATE

    def test_contains_score_range(self):
        assert "0_to_100" in REVIEWER_SYSTEM_TEMPLATE or "0-100" in REVIEWER_SYSTEM_TEMPLATE

    def test_contains_pass_threshold_hint(self):
        assert "score >= 70" in REVIEWER_SYSTEM_TEMPLATE or ">= 70" in REVIEWER_SYSTEM_TEMPLATE

    def test_contains_submit_command(self):
        assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in REVIEWER_SYSTEM_TEMPLATE

    def test_has_five_criteria(self):
        # FORMAT, GENERALIZABILITY, CAUSAL_DEPTH, ACTIONABILITY, DISTINCTIVENESS
        for criterion in ["FORMAT", "GENERALIZABILITY", "CAUSAL_DEPTH", "ACTIONABILITY", "DISTINCTIVENESS"]:
            assert criterion in REVIEWER_SYSTEM_TEMPLATE


class TestReviewerInstanceTemplate:
    def test_contains_instance_id_placeholder(self):
        assert "{{instance_id}}" in REVIEWER_INSTANCE_TEMPLATE

    def test_contains_rule_text_placeholder(self):
        assert "{{rule_text}}" in REVIEWER_INSTANCE_TEMPLATE

    def test_contains_file_list_placeholder(self):
        assert "{{file_list}}" in REVIEWER_INSTANCE_TEMPLATE
